from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

from ..services import naming


class ProcurementDeliveryDiscrepancy(models.Model):
    """A recorded delivery problem with its follow-up action, person and deadline.

    Requirements: FR-SPM-0060
    The record is the delivery evidence the supplier's scorecard reads.
    """
    _name = 'procurement.delivery.discrepancy'
    _description = 'Delivery Discrepancy'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'deadline, id'

    commitment_id = fields.Many2one('procurement.delivery.commitment', string="Commitment", required=True,
                                    ondelete='cascade', index=True)
    order_id = fields.Many2one(related='commitment_id.order_id', store=True, index=True)
    partner_id = fields.Many2one(related='commitment_id.partner_id', store=True, index=True)
    product_id = fields.Many2one(related='commitment_id.product_id', store=True)
    company_id = fields.Many2one(related='commitment_id.company_id', store=True, index=True)
    reason_id = fields.Many2one('procurement.discrepancy.reason', string="Reason", required=True,
                                ondelete='restrict')
    kind = fields.Selection(related='reason_id.kind', store=True)
    quantity = fields.Float(digits='Product Unit')
    action = fields.Char(string="Follow-up", required=True)
    responsible_id = fields.Many2one('res.users', string="Responsible", required=True, ondelete='restrict')
    deadline = fields.Date(required=True)
    state = fields.Selection([('open', 'Open'), ('done', 'Done')], default='open', required=True, readonly=True)

    _quantity_not_negative = models.Constraint('CHECK(quantity >= 0)', 'The quantity cannot be negative.')

    @api.depends('reason_id', 'commitment_id')
    def _compute_display_name(self):
        """Requirements: FR-SPM-0060

        The reason of the problem and the order line it concerns.
        """
        for discrepancy in self:
            discrepancy.display_name = naming.join_name(
                [discrepancy.reason_id.name, discrepancy.commitment_id.display_name], discrepancy._description)

    @api.model_create_multi
    def create(self, vals_list):
        """Store the discrepancies and give each responsible person an activity due on the deadline.

        Requirements: FR-SPM-0060
        """
        discrepancies = super().create([dict(vals, state='open') for vals in vals_list])
        for discrepancy in discrepancies:
            discrepancy.activity_schedule(
                'mail.mail_activity_data_todo', date_deadline=discrepancy.deadline,
                summary=discrepancy.action, user_id=discrepancy.responsible_id.id)
        return discrepancies

    def write(self, vals):
        """Refuse a direct change of the state: only the close action sets it.

        Requirements: FR-SPM-0060
        """
        if 'state' in vals and not self.env.su:
            raise AccessError(self.env._("Use the action to close a discrepancy."))
        return super().write(vals)

    def action_spm_done(self):
        """Close the follow-up: the activity of the responsible person is done too.

        Requirements: FR-SPM-0060
        """
        self.check_access('write')
        for discrepancy in self:
            if discrepancy.state != 'open':
                raise UserError(self.env._("This discrepancy is already closed."))
        self.activity_feedback(['mail.mail_activity_data_todo'])
        self._write_state_done()
        return True

    def _write_state_done(self):
        """Set the state; the field is read-only for every screen and written only by the action."""
        self.sudo().write({'state': 'done'})  # sudo: state is read-only; the caller's write right was checked by the action
