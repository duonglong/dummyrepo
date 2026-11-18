from odoo import models

from ..services import supplier_control


class PurchaseOrder(models.Model):
    """Purchase order guarded by supplier control.

    Requirements: BR-SPM-0006, FR-SPM-0013
    """
    _inherit = 'purchase.order'

    def button_confirm(self):
        """Refuse the confirmation when the supplier is not allowed.

        Requirements: BR-SPM-0006, FR-SPM-0013
        """
        for order in self:
            supplier_control.check_supplier_allowed(
                self.env, order, order.partner_id, order.company_id, 'confirm')
        return super().button_confirm()
