from datetime import datetime, time

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

from ..services import portal_access, supplier_control

ACK_FIELDS = {'spm_acknowledged_date', 'spm_acknowledged_user_id'}


class PurchaseOrder(models.Model):
    """Purchase order guarded by supplier control.

    Requirements: BR-SPM-0006, FR-SPM-0013
    """
    _inherit = 'purchase.order'

    spm_delivery_event_ids = fields.One2many('procurement.delivery.event', 'order_id', string="Delivery history")
    spm_commitment_ids = fields.One2many('procurement.delivery.commitment', 'order_id', string="Delivery commitments")
    spm_acknowledged_date = fields.Datetime(string="Acknowledged on", copy=False, readonly=True)
    spm_acknowledged_user_id = fields.Many2one('res.users', string="Acknowledged by", copy=False,
                                               readonly=True, ondelete='set null')

    @api.model_create_multi
    def create(self, vals_list):
        """Give the goods lines of an order created already confirmed their delivery commitment.

        Requirements: FR-SPM-0055, FR-SPM-0024
        An acknowledgement is recorded only by the supplier's action, never set on a new order.
        """
        if not self.env.su and any(ACK_FIELDS & set(vals) for vals in vals_list):
            raise AccessError(self.env._("Only the supplier acknowledges an order."))
        orders = super().create(vals_list)
        orders._spm_ensure_commitments()
        return orders

    def write(self, vals):
        """Give the goods lines of an order its delivery commitments when it becomes confirmed.

        Requirements: FR-SPM-0055, FR-SPM-0024
        """
        if not self.env.su and ACK_FIELDS & set(vals):
            raise AccessError(self.env._("Only the supplier acknowledges an order."))
        result = super().write(vals)
        if vals.get('state') == 'purchase':
            self._spm_ensure_commitments()
        return result

    def _spm_ensure_commitments(self):
        """Create the missing delivery commitment of each goods line of the confirmed orders.

        Requirements: FR-SPM-0055, FR-SPM-0061
        """
        self.order_line._spm_ensure_commitment()

    def _spm_require_own_supplier(self, refusal):
        """Return the order (elevated) when the caller is a portal user of its supplier; raise ``refusal`` if not.

        Requirements: FR-SPM-0024, FR-SPM-0025
        The id comes from the client: the order must exist, be readable by the caller, belong to an
        allowed company and to the caller's own supplier.
        """
        order = self.sudo().exists()  # sudo: the id is checked against the caller's own rights just below
        if not order or not self.env.user.share or order.company_id not in self.env.companies \
                or not self.has_access('read'):
            raise refusal
        try:
            supplier = portal_access.current_supplier(self.env)
        except portal_access.SupplierAccessRefused as supplier_refusal:
            raise refusal from supplier_refusal
        if order.partner_id.commercial_partner_id != supplier:
            raise refusal
        return order

    def spm_acknowledge(self, dates):
        """Record the supplier's acknowledgement and the confirmed date of each line.

        Requirements: FR-SPM-0024
        ``dates`` maps an order line id to the confirmed date; a line left out is confirmed for its
        expected date. Called by a portal user of the order's own supplier on a confirmed order.
        """
        self.ensure_one()
        order = self._spm_require_own_supplier(AccessError(self.env._("You cannot acknowledge this order.")))
        order.lock_for_update()  # two posts of the same form wait for each other and see the first one's mark
        order.invalidate_recordset(['spm_acknowledged_date'])
        if order.state != 'purchase':
            raise UserError(self.env._("Only a confirmed order can be acknowledged."))
        if order.spm_acknowledged_date:
            raise UserError(self.env._("This order is already acknowledged; propose a new date for a line instead."))
        commitments = order.spm_commitment_ids
        unknown = set(dates or {}) - set(commitments.order_line_id.ids)
        if unknown:
            raise UserError(self.env._("A date was given for a line that is not on this order."))
        for commitment in commitments:  # sudo: the supplier has no write right; every check passed above
            confirmed = (dates or {}).get(commitment.order_line_id.id)
            commitment.write({'confirmed_date': commitment.order_line_id._convert_to_middle_of_day(
                datetime.combine(fields.Date.to_date(confirmed), time.min)) if confirmed else commitment.expected_date})
        order.write({  # sudo: see above
            'spm_acknowledged_date': fields.Datetime.now(), 'spm_acknowledged_user_id': self.env.user.id})
        order.message_post(body=self.env._("Order acknowledged by %(user)s.", user=self.env.user.name),
                           subtype_xmlid='mail.mt_note')
        return True

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
