from odoo import models

from ..services import portal_access, supplier_control


class PurchaseOrder(models.Model):
    """Purchase order guarded by supplier control.

    Requirements: BR-SPM-0006, FR-SPM-0013
    """
    _inherit = 'purchase.order'

    def _spm_check_supplier(self):
        """Refuse when the supplier of an order is not allowed to be confirmed with.

        Requirements: BR-SPM-0006, FR-SPM-0013
        """
        for order in self:
            supplier_control.check_supplier_allowed(
                self.env, order, order.partner_id, order.company_id, 'confirm')

    def button_confirm(self):
        """Refuse the confirmation when the supplier is not allowed.

        Requirements: BR-SPM-0006, FR-SPM-0013
        """
        self._spm_check_supplier()
        return super().button_confirm()

    def button_approve(self, force=False):
        """Refuse the approval when the supplier is not allowed (the order may wait since its confirmation).

        Requirements: BR-SPM-0006, FR-SPM-0013
        """
        self._spm_check_supplier()
        return super().button_approve(force=force)

    def action_spm_open_override_wizard(self):
        """Open the reason dialog to record an override for this order.

        Requirements: FR-SPM-0013
        """
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._("Override supplier control"),
            'res_model': 'procurement.reason.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_res_model': self._name,
                'default_res_ids': self.ids,
                'default_action': 'override',
            },
        }

    def _get_share_message_domain(self):
        """Messages of the portal page: a supplier portal user sees only those to or from its company.

        Requirements: FR-SPM-0026
        """
        return portal_access.supplier_message_domain(self.env, super()._get_share_message_domain())
