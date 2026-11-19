from odoo import fields, models


class ProcurementContractRenew(models.TransientModel):
    """Dialog asking for the new end date of a contract.

    Requirements: FR-SPM-0066
    """
    _name = 'procurement.contract.renew'
    _description = 'Renew a Contract'

    contract_id = fields.Many2one('procurement.contract', required=True, ondelete='cascade')
    date_end = fields.Date(string="New end date", required=True)

    def action_confirm(self):
        """Renew the contract to the new end date.

        Requirements: FR-SPM-0066
        """
        self.ensure_one()
        self.contract_id.action_spm_renew(self.date_end)
        return {'type': 'ir.actions.act_window_close'}
