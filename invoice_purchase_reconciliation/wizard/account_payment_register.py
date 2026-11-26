from odoo import models


class AccountPaymentRegister(models.TransientModel):
    _inherit = 'account.payment.register'

    def _create_payments(self):
        """Refuse the payment of the whole selection when one vendor bill is held.

        Requirements: FR-IPR-0017
        """
        self.ensure_one()
        self.line_ids.move_id._ipr_refuse_payment()
        return super()._create_payments()
