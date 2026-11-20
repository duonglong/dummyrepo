from odoo import api, fields, models

OPEN_STATES = ('open', 'in_progress')


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
        ('open', 'Open'), ('in_progress', 'In progress'), ('done', 'Done'), ('cancelled', 'Cancelled'),
    ], required=True, default='open', tracking=True)
    partner_id = fields.Many2one(related='review_id.partner_id', store=True, index=True)
    company_id = fields.Many2one(related='review_id.company_id', store=True, index=True)

    def _spm_schedule_activity(self):
        """Give the responsible person one to-do due on the deadline of each open action."""
        for action in self.filtered(lambda a: a.state in OPEN_STATES):
            action.activity_schedule(
                'mail.mail_activity_data_todo', date_deadline=action.deadline, user_id=action.responsible_id.id,
                summary=action.name)

    @api.model_create_multi
    def create(self, vals_list):
        """Requirements: FR-SPM-0086"""
        actions = super().create(vals_list)
        actions._spm_schedule_activity()
        return actions

    def write(self, vals):
        """Requirements: FR-SPM-0086

        The activity follows the action: closed with it, moved with its owner or deadline.
        """
        result = super().write(vals)
        if {'state', 'responsible_id', 'deadline', 'name'} & set(vals):
            self.activity_unlink(['mail.mail_activity_data_todo'])
            self._spm_schedule_activity()
        return result
