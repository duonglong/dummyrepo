from odoo import api, fields, models

from ..services import portal_access, supplier_control


class PurchaseOrder(models.Model):
    """Purchase order guarded by supplier control.

    Requirements: BR-SPM-0006, FR-SPM-0013
    """
    _inherit = 'purchase.order'

    spm_delivery_event_ids = fields.One2many('procurement.delivery.event', 'order_id', string="Delivery history")
    spm_commitment_ids = fields.One2many('procurement.delivery.commitment', 'order_id', string="Delivery commitments")

    @api.model_create_multi
    def create(self, vals_list):
        """Give the goods lines of an order created already confirmed their delivery commitment.

        Requirements: FR-SPM-0055
        """
        orders = super().create(vals_list)
        orders._spm_ensure_commitments()
        return orders

    def write(self, vals):
        """Give the goods lines of an order its delivery commitments when it becomes confirmed.

        Requirements: FR-SPM-0055
        """
        result = super().write(vals)
        if vals.get('state') == 'purchase':
            self._spm_ensure_commitments()
        return result

    def _spm_ensure_commitments(self):
        """Create the missing delivery commitment of each goods line of the confirmed orders.

        Requirements: FR-SPM-0055, FR-SPM-0061
        """
        self.order_line._spm_ensure_commitment()

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
        """Messages of the portal page: the supplier sees only those to or from its own company.

        Requirements: FR-SPM-0026
        """
        return portal_access.supplier_message_domain(
            self.env, super()._get_share_message_domain(),
            self.sudo().partner_id.commercial_partner_id)  # sudo: ids of the page's own supplier only


class PurchaseOrderLine(models.Model):
    """Order line carrying its delivery commitment.

    Requirements: FR-SPM-0055
    """
    _inherit = 'purchase.order.line'

    spm_commitment_ids = fields.One2many('procurement.delivery.commitment', 'order_line_id')

    @api.model_create_multi
    def create(self, vals_list):
        """Give a goods line added to a confirmed order its delivery commitment.

        Requirements: FR-SPM-0055
        """
        lines = super().create(vals_list)
        lines._spm_ensure_commitment()
        return lines

    def _spm_ensure_commitment(self):
        """Create the missing commitment of the goods lines of confirmed orders; nothing else is written.

        Requirements: FR-SPM-0055, FR-SPM-0061
        """
        lines = self.filtered(
            lambda line: line.order_id.state == 'purchase' and not line.display_type
            and line.product_id.type == 'consu' and not line.spm_commitment_ids)
        if lines:
            # a Buyer confirming an order has no create right on commitments, which only the system creates
            self.env['procurement.delivery.commitment'].sudo().create(
                [{'order_line_id': line.id} for line in lines])
