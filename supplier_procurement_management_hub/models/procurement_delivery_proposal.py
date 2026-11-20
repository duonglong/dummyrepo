from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from ..services import delivery

STORED_BY_ACTIONS = {'state', 'refuse_reason', 'decided_by_id', 'decided_date'}
FROZEN = {'commitment_id', 'proposed_by_id', 'date', 'reason', 'line_ids'}


class ProcurementDeliveryProposal(models.Model):
    """A supplier's delivery date or split proposal that waits for the buyer's decision.

    Requirements: FR-SPM-0025, FR-SPM-0058
    A proposal changes nothing on the commitment until the Buyer accepts it.
    """
    _name = 'procurement.delivery.proposal'
    _description = 'Delivery Date Proposal'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'id desc'

    commitment_id = fields.Many2one('procurement.delivery.commitment', string="Commitment", required=True,
                                    ondelete='cascade', index=True, readonly=True)
    order_id = fields.Many2one(related='commitment_id.order_id', store=True, index=True)
    partner_id = fields.Many2one(related='commitment_id.partner_id', store=True, index=True)
    company_id = fields.Many2one(related='commitment_id.company_id', store=True, index=True)
    proposed_by_id = fields.Many2one('res.users', string="Proposed by", readonly=True, ondelete='set null')
    date = fields.Datetime(string="Proposed date", readonly=True)
    reason = fields.Text(required=True, readonly=True)
    line_ids = fields.One2many('procurement.delivery.proposal.line', 'proposal_id', string="Split", readonly=True)
    state = fields.Selection([
        ('pending', 'Pending'), ('accepted', 'Accepted'), ('refused', 'Refused'),
    ], default='pending', required=True, readonly=True, index=True)
    refuse_reason = fields.Text(readonly=True)
    decided_by_id = fields.Many2one('res.users', string="Decided by", readonly=True, ondelete='set null')
    decided_date = fields.Datetime(readonly=True)

    @api.constrains('line_ids', 'commitment_id')
    def _check_split_total(self):
        """Refuse a split whose quantities do not add up to the ordered quantity.

        Requirements: FR-SPM-0025
        """
        for proposal in self.filtered('line_ids'):
            commitment = proposal.commitment_id
            total = sum(proposal.line_ids.mapped('quantity'))
            if not delivery.schedules_match(total, commitment.ordered_qty, commitment._spm_rounding()):
                raise ValidationError(self.env._(
                    "The proposed quantities total %(total)s of %(ordered)s ordered.",
                    total=f"{total:g}", ordered=f"{commitment.ordered_qty:g}"))

    @api.model_create_multi
    def create(self, vals_list):
        """Store proposals as pending, whatever the values say; the system creates them for the supplier.

        Requirements: FR-SPM-0025
        """
        proposals = super().create(vals_list)
        if not self.env.su and proposals.filtered(
                lambda p: p.state != 'pending' or p.decided_by_id or p.decided_date or p.refuse_reason):
            raise AccessError(self.env._("A proposal starts pending."))
        return proposals

    def write(self, vals):
        """Refuse any change of the decision or of the proposal itself except through the actions.

        Requirements: FR-SPM-0058
        """
        if not self.env.su and set(vals) & (STORED_BY_ACTIONS | FROZEN):
            raise AccessError(self.env._("Use accept or refuse to decide a proposal."))
        return super().write(vals)

    def _spm_lock_pending(self):
        """Lock the proposal rows and refuse unless every one is still pending (decided once).

        Requirements: FR-SPM-0058
        """
        self.check_access('write')
        self.lock_for_update()
        self.invalidate_recordset(['state'])
        if self.filtered(lambda p: p.state != 'pending'):
            raise UserError(self.env._("This proposal has already been decided."))

    def _spm_tell_supplier(self, body):
        """Send the decision to the supplier user who proposed.

        Requirements: FR-SPM-0058
        """
        partner = self.proposed_by_id.partner_id
        self.message_post(body=body, partner_ids=partner.ids, subtype_xmlid='mail.mt_comment')

    def action_spm_accept(self):
        """Set the commitment's confirmed date and schedules to the proposal and move the open receipts.

        Requirements: FR-SPM-0058
        The receipt moves follow the native order line date, which Odoo propagates to open moves;
        no stock move is created or validated here.
        """
        self.ensure_one()
        self._spm_lock_pending()
        commitment = self.commitment_id
        if commitment.state != 'open':
            raise UserError(self.env._("Only an open delivery can take a new date."))
        lines = self.line_ids.sorted('date')
        first_date = lines[:1].date or self.date
        schedules = [(5, 0, 0)] + [(0, 0, {'date': line.date, 'quantity': line.quantity}) for line in lines]
        commitment.write({'confirmed_date': first_date, 'schedule_ids': schedules})
        commitment.order_line_id._update_date_planned(first_date)
        self.sudo().write({  # sudo: the decision fields are written only by this action, after the write right was checked
            'state': 'accepted', 'decided_by_id': self.env.user.id, 'decided_date': fields.Datetime.now()})
        self.activity_unlink(['mail.mail_activity_data_todo'])
        self._spm_tell_supplier(self.env._("Your delivery date proposal for %(order)s was accepted.",
                                           order=self.order_id.name))
        return True

    def action_spm_refuse(self, reason):
        """Keep the agreed dates and tell the supplier why.

        Requirements: FR-SPM-0058
        """
        self.ensure_one()
        reason = (reason or '').strip()
        if not reason:
            raise UserError(self.env._("A reason is required to refuse a proposal."))
        self._spm_lock_pending()
        self.sudo().write({  # sudo: the decision fields are written only by this action, after the write right was checked
            'state': 'refused', 'refuse_reason': reason, 'decided_by_id': self.env.user.id,
            'decided_date': fields.Datetime.now()})
        self.activity_unlink(['mail.mail_activity_data_todo'])
        self._spm_tell_supplier(self.env._(
            "Your delivery date proposal for %(order)s was refused: %(reason)s",
            order=self.order_id.name, reason=reason))
        return True

    def action_spm_open_refuse_wizard(self):
        """Open the reason dialog to refuse this proposal.

        Requirements: FR-SPM-0058
        """
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._("Refuse delivery proposal"),
            'res_model': 'procurement.reason.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_res_model': self._name,
                'default_res_ids': self.ids,
                'default_action': 'refuse_proposal',
            },
        }
