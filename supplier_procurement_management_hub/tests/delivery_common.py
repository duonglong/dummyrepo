"""Shared fixtures of the delivery acceptance tests (T-023, T-024).

Requirements: FR-SPM-0055, FR-SPM-0061

Names not fixed by the design are chosen here and recorded in the module notes:
- procurement.delivery.event.spm_record(order, kind, date, reference=None, carrier=None, eta=None)
  (model method; runs elevated for a portal user of the order's supplier);
- purchase.order.spm_acknowledge(dates) with dates {order line id: date}, a missing line
  keeping its expected date (model method; run as the supplier's portal user);
- procurement.delivery.commitment.spm_propose(reason, date=None, lines=None) with lines a list of
  {'date', 'quantity'} (model method; run as the supplier's portal user) returning the proposal;
- procurement.delivery.proposal.action_spm_accept() and action_spm_refuse(reason);
- the delivery commitment is found by order_line_id; reason kinds late, short, over, damaged, wrong_item.
The portal route and the reason dialog are left to the build: the tests call the methods.
"""
from freezegun import freeze_time

from .portal_common import SpmPortalCase


class SpmDeliveryCase(SpmPortalCase):

    def _dorder(self, supplier=None, qty=100.0, planned='2026-12-10 08:00:00', buyer=None, confirm=True):
        """A confirmed order of ``qty`` chairs expected on ``planned``."""
        order = self.env['purchase.order'].create({
            'partner_id': (supplier or self.acme).id,
            'user_id': (buyer or self.buyer).id,
            'order_line': [(0, 0, {
                'product_id': self.chair.id, 'name': 'Chair', 'product_qty': qty,
                'price_unit': 11.5, 'tax_ids': [(6, 0, [])], 'date_planned': planned,
            })],
        })
        if confirm:
            order.button_confirm()
            if order.state != 'purchase':
                order.button_approve(force=True)
        return order

    def _commitment(self, order):
        return self.env['procurement.delivery.commitment'].search(
            [('order_line_id', 'in', order.order_line.ids)])

    def _receive(self, order, qty, when, backorder=False):
        """Validate the open receipt of ``order`` with ``qty`` on ``when``."""
        picking = order.picking_ids.filtered(lambda p: p.state not in ('done', 'cancel'))[:1]
        with freeze_time(when):
            picking.move_ids.quantity = qty
            picking.move_ids.picked = True
            context = {'skip_backorder': True}
            if not backorder:
                context['picking_ids_not_to_backorder'] = picking.ids
            picking.with_context(**context).button_validate()
        self.env.flush_all()
        return picking

    def _return(self, picking, qty):
        ret = picking._create_return()
        ret.move_ids.write({'quantity': qty, 'to_refund': True})
        ret.move_ids.picked = True
        ret.with_context(skip_backorder=True,
                         picking_ids_not_to_backorder=ret.ids).button_validate()
        self.env.flush_all()

    def _move_count(self, order):
        return self.env['stock.move'].search_count([('purchase_line_id', 'in', order.order_line.ids)])
