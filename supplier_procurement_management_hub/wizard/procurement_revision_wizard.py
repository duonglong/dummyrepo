from odoo import fields, models


class ProcurementRevisionWizard(models.TransientModel):
    """The dialog where the Buyer asks one supplier for a revised quotation.

    Requirements: FR-SPM-0036
    """
    _name = 'procurement.revision.wizard'
    _description = 'Request a Revised Quotation'

    response_id = fields.Many2one('procurement.sourcing.response', required=True, readonly=True)
    note_to_supplier = fields.Text(string="Note to the supplier", required=True)
    new_deadline = fields.Datetime(required=True)
    internal_note = fields.Text()

    def action_confirm(self):
        """Request the revision of the response with the typed values.

        Requirements: FR-SPM-0036
        """
        self.ensure_one()
        self.response_id.action_spm_request_revision(
            self.note_to_supplier, self.new_deadline, internal_note=self.internal_note)
        return {'type': 'ir.actions.act_window_close'}
