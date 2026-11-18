from odoo import fields, models
from odoo.exceptions import UserError


class ProcurementReasonWizard(models.TransientModel):
    """The mandatory reason dialog of a reasoned action.

    Requirements: FR-SPM-0011
    """
    _name = 'procurement.reason.wizard'
    _description = 'Reason Dialog'

    res_model = fields.Char(required=True)
    res_ids = fields.Json(required=True)
    action = fields.Selection([('set_status', 'Change supplier status')], required=True)
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
        return {'type': 'ir.actions.act_window_close'}
