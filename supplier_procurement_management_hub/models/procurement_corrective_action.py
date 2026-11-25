from odoo import api, fields, models
from odoo.exceptions import UserError

TODO_ACTIVITY = 'mail.mail_activity_data_todo'
OPEN_STATES = ('open',)


class ProcurementCorrectiveAction(models.Model):
    """An action of a review with a responsible person, a deadline and a status; its owner gets an activity.

    Requirements: FR-SPM-0086
    """
    _name = 'procurement.corrective.action'
    _description = 'Corrective Action'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'deadline, id'

    review_id = fields.Many2one('procurement.supplier.review', string="Review", required=True,
                                ondelete='cascade', index=True)
    name = fields.Char(string="Action", required=True)
    responsible_id = fields.Many2one('res.users', string="Responsible", required=True, ondelete='restrict')
    deadline = fields.Date(required=True)
    state = fields.Selection([
        ('open', 'Open'), ('done', 'Done'), ('cancelled', 'Cancelled'),
    ], required=True, default='open', tracking=True)
    partner_id = fields.Many2one(related='review_id.partner_id', store=True, index=True)
    company_id = fields.Many2one(related='review_id.company_id', store=True, index=True)

    def _spm_schedule_activity(self):
        """Give the responsible person one to-do due on the deadline of each open action.

        Requirements: FR-SPM-0086
        """
        for action in self.filtered(lambda a: a.state == 'open'):
            action.activity_schedule(
                TODO_ACTIVITY, date_deadline=action.deadline, user_id=action.responsible_id.id, summary=action.name)

    @api.model_create_multi
    def create(self, vals_list):
        """Requirements: FR-SPM-0086"""
        actions = super().create(vals_list)
        actions._spm_schedule_activity()
        return actions

    def write(self, vals):
        """Requirements: FR-SPM-0086

        The activity follows the action: marked done with it, moved with its owner or deadline.
        """
        reopened = self.filtered(lambda a: a.state != 'open') if vals.get('state') == 'open' else self.browse()
        result = super().write(vals)
        closing = self.filtered(lambda a: a.state != 'open')
        if closing:
            closing.activity_feedback([TODO_ACTIVITY], feedback=self.env._("Action closed."))
        if reopened:
            reopened._spm_schedule_activity()
        moved = (self - closing - reopened)
        if moved and {'responsible_id', 'deadline', 'name'} & set(vals):
            moved.activity_reschedule(
                [TODO_ACTIVITY], date_deadline=vals.get('deadline'), new_user_id=vals.get('responsible_id'))
            if 'name' in vals:
                moved.activity_search([TODO_ACTIVITY]).write({'summary': vals['name']})
        return result

    def _spm_close(self, state):
        """Move the open actions to ``state`` (done or cancelled)."""
        if self.filtered(lambda a: a.state != 'open'):
            raise UserError(self.env._("Only an open action can be closed. Reopen it first, or create a new action."))
        self.write({'state': state})
        return True

    def action_done(self):
        """Mark the action as done.

        Requirements: FR-SPM-0086
        """
        return self._spm_close('done')

    def action_cancel(self):
        """Cancel the action.

        Requirements: FR-SPM-0086
        """
        return self._spm_close('cancelled')

    def action_reopen(self):
        """Reopen a closed action; its to-do comes back.

        Requirements: FR-SPM-0086
        """
        if self.filtered(lambda a: a.state == 'open'):
            raise UserError(self.env._("This action is already open."))
        self.write({'state': 'open'})
        return True
