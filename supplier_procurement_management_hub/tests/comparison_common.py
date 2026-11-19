"""Shared fixtures of the milestone 3 sourcing acceptance tests (T-016, T-017).

Requirements: FR-SPM-0035, FR-SPM-0037, FR-SPM-0038, FR-SPM-0039, BR-SPM-0007, BR-SPM-0008

Names not fixed by the design are chosen here and recorded in the module notes:
- procurement.sourcing.event.action_spm_compare() (the "Compare" button: rebuilds the
  comparison and returns the window action of the procurement.comparison of the event);
  action_spm_evaluate() (rebuilds it, scores and ranks the responses, returns True);
  action_spm_generate_orders() (the "Generate orders" button, returns an action or a
  notification; a refusal may also be a UserError);
- procurement.sourcing.response.action_spm_request_revision(note_to_supplier, new_deadline,
  internal_note=None) (the "Request revision" button once its dialog is filled);
- comparison cells (procurement.comparison.line): row_kind 'line' is the unit price of one
  event line and one response (raw_value, normalized_value, is_best, missing); row_kind
  'criterion' is one configured criterion for one response (raw_value, score, manual_score,
  manual_comment); row_kind 'total' is the quotation total of one response (raw_value,
  missing true when the offer is incomplete); a lower price, total and lead time is best;
- criteria keep the kind and direction values of the module ('price', 'lead_time', 'manual';
  'lower', 'higher');
- a quotation of another currency or unit is stored directly as a version (the module's
  quotation entry takes the event's currency and the event line's unit).
"""
from datetime import datetime

from freezegun import freeze_time

from .portal_common import SpmPortalCase

DEADLINE = datetime(2026, 11, 30, 17, 0, 0)


class SpmComparisonCase(SpmPortalCase):

    def setUp(self):
        super().setUp()
        env = self.env
        self.desk = env['product.product'].create({'name': 'Desk M3', 'type': 'consu'})
        self.eur = env.ref('base.EUR')
        self.usd = env.ref('base.USD')
        (self.eur | self.usd).write({'active': True})
        self.box = env['uom.uom'].create({
            'name': 'Box of 10 M3', 'relative_uom_id': self.unit.id, 'relative_factor': 10.0})

    # events ----------------------------------------------------------------
    def _sourcing(self, partners, lines=None, criteria=(), deadline=DEADLINE, **extra):
        """A sent event of the buyer. ``lines``: (name, product, quantity) tuples,
        by default 60 chairs; ``criteria``: (name, kind, direction, weight) tuples."""
        lines = lines or [('Chair', self.chair, 60.0)]
        with freeze_time('2026-11-17 09:00:00'):
            event = self.env['procurement.sourcing.event'].with_user(self.buyer).create(dict({
                'title': 'SE M3', 'buyer_id': self.buyer.id, 'deadline': deadline,
                'invited_partner_ids': [(6, 0, partners.ids)],
                'line_ids': [(0, 0, {
                    'name': name, 'product_id': product.id, 'quantity': qty,
                    'uom_id': self.unit.id}) for name, product, qty in lines],
                'criterion_ids': [(0, 0, {
                    'name': name, 'kind': kind, 'direction': direction, 'weight': weight})
                    for name, kind, direction, weight in criteria],
            }, **extra))
            event.with_user(self.buyer).action_spm_send()
        return event

    def _assume(self, event, currency=None):
        """State every comparison assumption of the event (BR-SPM-0007)."""
        event.with_user(self.buyer).write({
            'currency_id': (currency or event.currency_id).id,
            'rate_date': '2026-11-30', 'tax_basis': 'excluded'})
        event.line_ids.with_user(self.buyer).write({'comparison_uom_id': self.unit.id})

    def _quote(self, response, offers, currency=None, uom=None, **terms):
        """Store a current version. ``offers``: (event line, price, lead time days) tuples."""
        event = response.event_id
        version = self.env['procurement.sourcing.response.version'].sudo().with_context(
            spm_new_version=True).create(dict({
                'response_id': response.id, 'version': 1, 'is_current': True,
                'submitted_date': '2026-11-20 10:00:00',
                'currency_id': (currency or event.currency_id).id,
                'line_ids': [(0, 0, {
                    'event_line_id': line.id, 'quantity': line.quantity,
                    'uom_id': (uom or line.uom_id).id, 'price_unit': price,
                    'discount': 0.0, 'lead_time_days': lead}) for line, price, lead in offers],
            }, **terms))
        response.sudo().write({'state': 'submitted', 'current_version_id': version.id})
        return version

    def _set_rate(self, foreign, per_company_unit, date='2026-11-30'):
        """``per_company_unit`` units of ``foreign`` for one unit of the company currency."""
        self.env['res.currency.rate'].create({
            'currency_id': foreign.id, 'company_id': self.company.id,
            'rate': per_company_unit, 'name': date})

    # comparison ------------------------------------------------------------
    def _compare(self, event, user=None):
        action = event.with_user(user or self.buyer).action_spm_compare()
        self.env.flush_all()
        self.assertEqual(action.get('res_model'), 'procurement.comparison')
        comparison = self.env['procurement.comparison'].search([('event_id', '=', event.id)])
        self.assertEqual(len(comparison), 1)
        return comparison

    def _cell(self, comparison, response, kind='line', line=None, criterion=None):
        cells = comparison.line_ids.filtered(
            lambda c: c.row_kind == kind and c.response_id == response
            and (not line or c.event_line_id == line)
            and (not criterion or c.criterion_id == criterion))
        self.assertEqual(len(cells), 1, f"one {kind} cell expected for {response.partner_id.name}")
        return cells
