"""Page speed: pagination at 20 rows, indexed search fields and a portal page without a query per row.

Requirements: NFR-SPM-0004, NFR-SPM-0005
The 2 second targets of the two requirements are measured on the large databases of the
release gate; these tests hold the properties that keep them reachable: a page never lists
more than 20 records, the fields every list and portal page searches are indexed, and the
queries of a page do not grow with the number of rows.
"""
import re

from ..services import portal_access
from .portal_common import SpmPortalCase

# model: the fields its lists, portal pages and jobs search or group by
INDEXED_FIELDS = {
    'procurement.sourcing.response': ('partner_id', 'event_id'),
    'procurement.supplier.document': ('partner_id',),
    'procurement.delivery.commitment': ('order_id',),
    'procurement.approval.request': ('order_id',),
    'procurement.audit.log': ('date', 'event'),
    'procurement.notification.log': ('kind', 'recipient_partner_id'),
    'procurement.reconciliation.exception': ('reconciliation_id',),
}


class TestPortalPagination(SpmPortalCase):
    """Requirements: NFR-SPM-0004"""

    def test_nfr_0004_a_page_lists_at_most_20_orders(self):
        """NFR-SPM-0004: 25 orders give a first page of 20 and a second page of 5, with a pager."""
        self.assertEqual(portal_access.PORTAL_PAGE_SIZE, 20)
        orders = self.env['purchase.order']
        for _index in range(25):
            orders |= self._order(self.acme)
        self._login(self.acme_user)
        names = set(orders.mapped('name'))

        def listed(address):
            text = self._get(address).text
            return {name for name in names if re.search(rf'\b{re.escape(name)}\b', text)}
        first = listed('/my/procurement')
        second = listed('/my/procurement/page/2')
        self.assertEqual(len(first), 20)
        self.assertEqual(len(second), 5)
        self.assertFalse(first & second)
        self.assertIn('/my/procurement/page/2', self._get('/my/procurement').text)

    def test_nfr_0004_the_queries_of_a_full_page_stay_bounded(self):
        """NFR-SPM-0004: the rows of a full page of 20 orders are read in a bounded number of queries."""
        orders = self.env['purchase.order']
        for _index in range(portal_access.PORTAL_PAGE_SIZE):
            orders |= self._order(self.acme)
        env = self.env(user=self.acme_user)
        self.env.flush_all()
        self.env.invalidate_all()
        before = self.cr.sql_log_count
        portal_access.order_rows(env, orders.with_env(env))
        self.assertLessEqual(self.cr.sql_log_count - before, 2 * portal_access.PORTAL_PAGE_SIZE + 10)


class TestIndexedSearchFields(SpmPortalCase):
    """Requirements: NFR-SPM-0005"""

    def test_nfr_0005_the_searched_fields_are_indexed(self):
        """NFR-SPM-0005: the fields of the lists and jobs named by the design carry a database index."""
        unindexed = [f'{model}.{name}' for model, names in INDEXED_FIELDS.items() for name in names
                     if not self.env[model]._fields[name].index]
        self.assertEqual(unindexed, [])
