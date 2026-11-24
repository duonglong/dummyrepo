from datetime import timedelta

from odoo import api, fields, models
from odoo.fields import Domain

from .procurement_reconciliation_exception import UNDECIDED_STATES

# xml id of the window action each tile starts from, per tile key
TILE_ACTIONS = {
    'closing_events': 'supplier_procurement_management_hub.procurement_sourcing_event_action',
    'expiring_documents': 'supplier_procurement_management_hub.procurement_supplier_document_action',
    'overdue_deliveries': 'supplier_procurement_management_hub.procurement_delivery_commitment_action',
    'invoice_exceptions': 'supplier_procurement_management_hub.procurement_reconciliation_exception_action',
    'open_requests': 'supplier_procurement_management_hub.procurement_request_action',
    'spend_month': 'supplier_procurement_management_hub.procurement_report_spend_action',
}
OPEN_REQUEST_STATES = ['submitted', 'sourcing']


class ProcurementDashboard(models.AbstractModel):
    """The service behind the procurement dashboard tiles: counts with the caller's own rights.

    Requirements: FR-SPM-0098
    """
    _name = 'procurement.dashboard'
    _description = 'Procurement Dashboard'

    @api.model
    def get_tiles(self):
        """Return the work-queue tiles the session user may see, each with its count and the list it opens.

        Requirements: FR-SPM-0098
        Each tile is a dict with key, name, count and action (a window action carrying the tile's
        domain); the spend tile also carries the amount and its currency. A tile whose records the
        user may not read is left off, and every count runs through his record rules and companies.
        """
        tiles = [
            self._approvals_tile(),
            self._queue_tile('closing_events', self.env._("Sourcing events closing soon"),
                             'procurement.sourcing.event', self._closing_events_domain()),
            self._queue_tile('expiring_documents', self.env._("Supplier documents expiring or expired"),
                             'procurement.supplier.document', [('is_current', '=', True), ('validity_state', 'in', ['expiring', 'expired'])]),
            self._queue_tile('overdue_deliveries', self.env._("Overdue deliveries"),
                             'procurement.delivery.commitment', [('is_overdue', '=', True)]),
            self._queue_tile('invoice_exceptions', self.env._("My open invoice exceptions"),
                             'procurement.reconciliation.exception',
                             [('state', 'in', list(UNDECIDED_STATES)), ('owner_id', '=', self.env.uid)]),
            self._queue_tile('open_requests', self.env._("Open purchase requests"),
                             'procurement.request', [('state', 'in', OPEN_REQUEST_STATES)]),
            self._spend_tile(),
        ]
        return [tile for tile in tiles if tile]

    @api.model
    def _approvals_tile(self):
        """The steps waiting for the user (his own and those of the approvers he stands in for)."""
        step = self.env['procurement.approval.step']
        if not step.has_access('read'):
            return None
        action = step.action_spm_open_inbox()
        action['name'] = self.env._("Approvals waiting for me")
        return {'key': 'approvals', 'name': action['name'],
                'count': step.search_count(action['domain']), 'action': action}

    @api.model
    def _closing_events_domain(self):
        """Open events whose deadline falls within each allowed company's closing-soon days."""
        now = fields.Datetime.now()
        closing = Domain.OR([
            Domain('company_id', '=', company.id)
            & Domain('deadline', '>=', fields.Datetime.to_string(now))
            & Domain('deadline', '<=', fields.Datetime.to_string(now + timedelta(days=company.spm_closing_soon_days)))
            for company in self.env.companies
        ])
        return list(Domain('state', '=', 'open') & closing)

    @api.model
    def _queue_tile(self, key, name, model, domain):
        """A count of the records of ``model`` matching ``domain`` that the user may read, and the list."""
        records = self.env[model]
        if not records.has_access('read'):
            return None
        action = self._tile_action(key, name, domain)
        return {'key': key, 'name': name, 'count': records.search_count(domain), 'action': action}

    @api.model
    def _tile_action(self, key, name, domain):
        """The tile's native window action, narrowed to the domain, with no default filter."""
        action = self.env['ir.actions.act_window']._for_xml_id(TILE_ACTIONS[key])
        action.update({'name': name, 'domain': domain, 'context': {'create': False}})
        return action

    @api.model
    def _spend_tile(self):
        """Spend of the current month in the user's company currency; none for users without the spend report."""
        spend = self.env['procurement.report.spend']
        if not spend.has_access('read'):
            return None
        today = fields.Date.context_today(self)
        domain = [('date', '>=', fields.Date.to_string(today.replace(day=1))), ('date', '<=', fields.Date.to_string(today))]
        company = self.env.company
        total = 0.0
        for report_company, amount in spend._read_group(domain, ['company_id'], ['amount_untaxed:sum']):
            total += report_company.currency_id._convert(amount or 0.0, company.currency_id, report_company, today)
        name = self.env._("Spend this month")
        return {
            'key': 'spend_month', 'name': name, 'count': spend.search_count(domain), 'amount': total,
            'currency_id': company.currency_id.id, 'action': self._tile_action('spend_month', name, domain),
        }
