from odoo import fields, models
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
        Rights, scope and audit are those of the override's create; a record
        that is refused for neither reason gets a status override as before.
        """
        records = self.env[self.res_model].browse(self.res_ids)
        values = []
        for record in records:
            kinds = self.res_model in supplier_control.CONTROLLED_MODELS and supplier_control.refusal_kinds(
                self.env, record, record.partner_id, record.company_id, 'confirm') or ['status']
            values += [{
                'res_model': self.res_model, 'res_id': record.id, 'action': 'confirm',
                'kind': kind, 'reason': self.reason,
            } for kind in kinds]
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
