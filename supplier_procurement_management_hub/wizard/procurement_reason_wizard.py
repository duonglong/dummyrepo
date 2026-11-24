from odoo import api, fields, models
from odoo.exceptions import UserError

from ..services import supplier_control


class ProcurementReasonWizard(models.TransientModel):
    """The mandatory reason dialog of a reasoned action.

    Requirements: FR-SPM-0011
    """
    _name = 'procurement.reason.wizard'
    _description = 'Reason Dialog'

    res_model = fields.Char(required=True)
    res_ids = fields.Json(required=True)
    action = fields.Selection([
        ('set_status', 'Change supplier status'), ('override', 'Override supplier control'),
        ('request_changes', 'Request changes on an application'), ('reject', 'Reject an application'),
        ('accept_exception', 'Accept an exception'), ('reject_exception', 'Reject an exception'),
        ('resolve_exception', 'Resolve an exception'), ('not_duplicate', 'Mark a bill as not a duplicate'),
        ('cancel', 'Cancel a sourcing event'), ('refuse_proposal', 'Refuse a delivery date proposal'),
    ], required=True)
    status_id = fields.Many2one('procurement.supplier.status', string="New status")
    status_kind = fields.Selection(related='status_id.kind')
    reason = fields.Text()
    override_action = fields.Selection(
        [('invite', 'Invite'), ('award', 'Award')], string="Override for",
        help="The step of the sourcing event the override lets this supplier go through.")
    override_partner_id = fields.Many2one(
        'res.partner', string="Supplier", help="The supplier of the event the override covers.")
    override_partner_ids = fields.Many2many(
        'res.partner', compute='_compute_override_partner_ids',
        help="The suppliers invited to the event or having answered it.")

    @api.depends('res_model', 'res_ids')
    def _compute_override_partner_ids(self):
        """List the suppliers an override of a sourcing event may name.

        Requirements: FR-SPM-0090
        """
        for wizard in self:
            event = wizard.env['procurement.sourcing.event']
            if wizard.res_model == event._name and wizard.res_ids:
                event = event.browse(wizard.res_ids[:1])
                wizard.override_partner_ids = (
                    event.invited_partner_ids | event.response_ids.partner_id).commercial_partner_id
            else:
                wizard.override_partner_ids = False

    def action_confirm(self):
        """Run the reasoned action on the records and close the dialog.

        Requirements: FR-SPM-0011
        """
        self.ensure_one()
        if self.action == 'set_status':
            if not self.status_id:
                raise UserError(self.env._("Choose the new status."))
            if self.res_model != 'res.partner':
                raise UserError(self.env._("The status can only be changed on suppliers."))
            self.env['res.partner'].browse(self.res_ids).action_spm_set_status(
                self.status_id, self.reason)
        if self.action == 'override':
            self._spm_record_overrides()
        if self.action in ('request_changes', 'reject'):
            self._spm_review_applications()
        if self.action in ('accept_exception', 'reject_exception', 'resolve_exception', 'not_duplicate'):
            self._spm_decide_matching()
        if self.action == 'cancel':
            self._spm_cancel_events()
        if self.action == 'refuse_proposal':
            self._spm_refuse_proposals()
        return {'type': 'ir.actions.act_window_close'}

    def _spm_refuse_proposals(self):
        """Forward the reason to the refusal of the delivery date proposals.

        Requirements: FR-SPM-0058
        Rights, state and the required reason are those of the proposal's action.
        """
        if self.res_model != 'procurement.delivery.proposal':
            raise UserError(self.env._("This action only applies to delivery date proposals."))
        for proposal in self.env[self.res_model].browse(self.res_ids):
            proposal.action_spm_refuse(self.reason)

    def _spm_cancel_events(self):
        """Forward the reason to the cancellation of the sourcing events.

        Requirements: FR-SPM-0042
        Rights, state and the required reason are those of the event's action.
        """
        if self.res_model != 'procurement.sourcing.event':
            raise UserError(self.env._("This action only applies to sourcing events."))
        self.env[self.res_model].browse(self.res_ids).action_spm_cancel(self.reason)

    def _spm_decide_matching(self):
        """Forward the comment to the decision of the exceptions or reconciliations.

        Requirements: FR-SPM-0075, FR-SPM-0077
        Rights and state are those of the decision methods; the comment is
        required here for every decision, resolve included.
        """
        if not (self.reason or '').strip():
            raise UserError(self.env._("A comment is required to decide."))
        expected = {
            'accept_exception': 'procurement.reconciliation.exception',
            'reject_exception': 'procurement.reconciliation.exception',
            'resolve_exception': 'procurement.reconciliation.exception',
            'not_duplicate': 'procurement.reconciliation',
        }[self.action]
        if self.res_model != expected:
            raise UserError(self.env._("This action does not apply to these records."))
        records = self.env[self.res_model].browse(self.res_ids)
        method = {
            'accept_exception': 'action_spm_accept', 'reject_exception': 'action_spm_reject',
            'resolve_exception': 'action_spm_resolve', 'not_duplicate': 'action_spm_mark_not_duplicate',
        }[self.action]
        getattr(records, method)(self.reason)

    def _spm_record_overrides(self):
        """Record the overrides each record needs (status, risk) to be confirmed with its supplier.

        Requirements: FR-SPM-0013, FR-SPM-0090
        Rights, scope and audit are those of the override's create; nothing is
        recorded, and the manager told, when no record is refused.
        """
        records = self.env[self.res_model].browse(self.res_ids)
        is_event = self.res_model == 'procurement.sourcing.event'
        if is_event and not (self.override_action and self.override_partner_id):
            raise UserError(self.env._("Choose the step and the supplier the override is for."))
        action = self.override_action if is_event else 'confirm'
        values = []
        for record in records:
            if self.res_model in supplier_control.CONTROLLED_MODELS:
                partner = (supplier_control.override_partner(self.env, record, self.override_partner_id.id)
                           if is_event else record.partner_id)
                kinds = supplier_control.refusal_kinds(
                    self.env, record, partner, record.company_id, action)
            else:
                kinds = ['status']
            values += [{
                'res_model': self.res_model, 'res_id': record.id, 'action': action,
                'kind': kind, 'reason': self.reason, 'partner_id': self.override_partner_id.id,
            } for kind in kinds]
        if not values:
            raise UserError(self.env._("Nothing is refused for this supplier: no override is needed."))
        self.env['procurement.override'].create(values)

    def _spm_review_applications(self):
        """Forward the reason to the review action of the applications.

        Requirements: FR-SPM-0004, FR-SPM-0006
        Rights, state and the required reason are those of the application's actions.
        """
        if self.res_model != 'procurement.supplier.application':
            raise UserError(self.env._("This action only applies to supplier applications."))
        applications = self.env[self.res_model].browse(self.res_ids)
        if self.action == 'reject':
            applications.action_spm_reject(self.reason)
        else:
            applications.action_spm_request_changes(self.reason)
