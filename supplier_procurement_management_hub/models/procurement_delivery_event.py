from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

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
    origin = fields.Selection([('buyer', 'Buyer'), ('portal', 'Supplier portal')], readonly=True)
    user_id = fields.Many2one('res.users', string="Recorded by", readonly=True, ondelete='set null')

    @api.model_create_multi
    def create(self, vals_list):
        """Take author and origin from the session, whatever the caller sends.

        Requirements: FR-SPM-0057
        """
        origin = 'portal' if self.env.user.share else 'buyer'
        events = super().create([
            dict(vals, origin=origin, user_id=self.env.user.id) for vals in vals_list])
        events.order_id.invalidate_recordset(['spm_delivery_event_ids'])  # the history stays in date order
        return events

    @api.model
    def spm_record(self, order, kind, date, reference=None, carrier=None, eta=None, note=None):
        """Record a shipment event on an order the caller can read, as a Buyer or as the supplier's portal user.

        Requirements: FR-SPM-0057, FR-SPM-0061
        A portal user has no create right on events: the order is checked through the caller's own
        rights and company first, then only the event itself is created with elevated rights.
        """
        order = self.env['purchase.order'].browse(order.id if hasattr(order, 'id') else order).exists()
        if not order or not order.has_access('read') or order.company_id not in self.env.companies:
            raise AccessError(self.env._("You cannot record a shipment event on this order."))
        if order.state not in ('purchase', 'done'):
            raise UserError(self.env._("Shipment events are recorded on confirmed orders."))
        return self.sudo().create({  # a plain portal user lacks the create right on events
            'order_id': order.id, 'kind': kind, 'date': date, 'shipment_reference': reference,
            'carrier': carrier, 'eta': eta, 'note': note,
        })
