from odoo import api, models

BILLS_TO_APPROVE_MENUS = (
    'invoice_purchase_reconciliation.ipr_bill_to_approve_menu_account',
    'invoice_purchase_reconciliation.ipr_bill_to_approve_menu_purchase',
)


class IrUiMenu(models.Model):
    _inherit = 'ir.ui.menu'

    @api.model
    def load_web_menus(self, debug):
        """Show the number of bills waiting for approval on the Bills to Approve entries.

        Requirements: FR-IPR-0011
        """
        web_menus = super().load_web_menus(debug)
        menu_ids = [
            menu.id for xmlid in BILLS_TO_APPROVE_MENUS
            if (menu := self.env.ref(xmlid, raise_if_not_found=False)) and menu.id in web_menus]
        if not menu_ids:
            return web_menus
        waiting = self.env['account.move'].search_count([
            ('move_type', '=', 'in_invoice'), ('ipr_state', '=', 'waiting')])
        if waiting:
            for menu_id in menu_ids:
                web_menus[menu_id]['name'] = f"{web_menus[menu_id]['name']} ({waiting})"
        return web_menus
