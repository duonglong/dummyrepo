from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

from ..services import portal_access

EVENT_KINDS = [
    ('shipped', 'Shipped'), ('in_transit', 'In transit'), ('delayed', 'Delayed'), ('delivered', 'Delivered'),
]


class ProcurementDeliveryEvent(models.Model):
    """A shipment event of an order: informative history, never a receipt.

    Requirements: FR-SPM-0057, FR-SPM-0061
    """
    _name = 'procurement.delivery.event'
    _description = 'Delivery Event'
    _order = 'date, id'

    order_id = fields.Many2one('purchase.order', string="Order", required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one(related='order_id.company_id', store=True, index=True)
    kind = fields.Selection(EVENT_KINDS, required=True)
    date = fields.Datetime(required=True)
    shipment_reference = fields.Char()
    carrier = fields.Char()
    eta = fields.Datetime(string="Estimated arrival")
    note = fields.Text()
    origin = fields.Selection([('backoffice', 'Back office'), ('portal', 'Supplier portal')], readonly=True)
    user_id = fields.Many2one('res.users', string="Recorded by", readonly=True, ondelete='set null')

    @api.model_create_multi
    def create(self, vals_list):
        """Take author and origin from the session, whatever the caller sends.

        Requirements: FR-SPM-0057
        """
        origin = 'portal' if self.env.user.share else 'backoffice'
        events = super().create([
            dict(vals, origin=origin, user_id=self.env.user.id) for vals in vals_list])
        events._check_order_confirmed()
        events.order_id.invalidate_recordset(['spm_delivery_event_ids'])  # the history stays in date order
        return events

    def write(self, vals):
        """Requirements: FR-SPM-0057"""
        result = super().write(vals)
        if 'order_id' in vals:
            self._check_order_confirmed()
        return result

    def _check_order_confirmed(self):
        """Refuse an event on an order that is not confirmed, whatever path stored it.

        Requirements: FR-SPM-0057
        """
        if self.order_id.filtered(lambda order: order.state not in ('purchase', 'done')):
            raise UserError(self.env._("Shipment events are recorded on confirmed orders."))

    @api.model
    def spm_record(self, order, kind, date, reference=None, carrier=None, eta=None, note=None):
        """Record a shipment event on an order the caller can read, as a Buyer or as the supplier's portal user.

        Requirements: FR-SPM-0057, FR-SPM-0061
        An internal user creates under the event access rows (Buyer, Manager, Admin only). A portal
        user has no create right: the order must belong to the user's own supplier, then only the
        event itself is created with elevated rights.
        """
        order = order.exists()
        if not order or not order.has_access('read') or order.company_id not in self.env.companies:
            raise AccessError(self.env._("You cannot record a shipment event on this order."))
        values = {
            'order_id': order.id, 'kind': kind, 'date': date, 'shipment_reference': reference,
            'carrier': carrier, 'eta': eta, 'note': note,
        }
        if not self.env.user.share:
            return self.create(values)
        try:
            supplier = portal_access.current_supplier(self.env)
        except portal_access.SupplierAccessRefused as refused:
            raise AccessError(self.env._("You cannot record a shipment event on this order.")) from refused
        if order.partner_id.commercial_partner_id != supplier:
            raise AccessError(self.env._("You cannot record a shipment event on this order."))
        return self.sudo().create(values)  # sudo: a portal user has no create right; own order and supplier checked above
