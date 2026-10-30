import time
from datetime import datetime

from odoo import Command
from odoo.tests.common import tagged

from ..models.sale_order import SaleOrder
from .common import WeekendMultiplierCase

LINES = 100
MAX_EXTRA_SECONDS = 0.05
MAX_EXTRA_QUERIES = 2
RUNS = 5


@tagged('post_install', '-at_install')
class TestPerformance(WeekendMultiplierCase):
    """The weekend check stays negligible on a 100-line order.

    Requirements: NFR-PERF-001
    """

    def _make_order(self, date_order):
        return self.env['sale.order'].with_user(self.user_no_tz).create({
            'partner_id': self.partner.id,
            'date_order': date_order,
            'order_line': [Command.create({
                'product_id': self.product.id,
                'product_uom_qty': 1,
                'price_unit': 10.0,
                'tax_ids': [Command.clear()],
            }) for _ in range(LINES)],
        })

    def _native_compute(self, order, programs):
        """The points computation without this module (parent implementation)."""
        return super(SaleOrder, order)._program_check_compute_points(programs)

    def _measure(self, func, runs=RUNS):
        """Return (best elapsed seconds, queries of one run) of ``func``."""
        best = None
        queries = 0
        for _run in range(runs):
            before = self.cr.sql_log_count
            started = time.perf_counter()
            func()
            elapsed = time.perf_counter() - started
            queries = self.cr.sql_log_count - before
            best = elapsed if best is None else min(best, elapsed)
        return best, queries

    def _measure_both(self):
        self.program.weekend_multiplier = 2.0
        order = self._make_order(datetime(2026, 10, 24, 10, 0))  # Saturday
        programs = self.program
        # warm the caches so both runs read the same data
        order._program_check_compute_points(programs)
        self._native_compute(order, programs)
        native = self._measure(lambda: self._native_compute(order, programs))
        module = self._measure(lambda: order._program_check_compute_points(programs))
        return native, module

    def test_query_overhead_le_2(self):
        """Requirements: NFR-PERF-001 - at most 2 queries more."""
        (_native_time, native_queries), (_module_time, module_queries) = self._measure_both()
        self.assertLessEqual(module_queries - native_queries, MAX_EXTRA_QUERIES)

    def test_time_overhead_le_50ms(self):
        """Requirements: NFR-PERF-001 - at most 50 ms more."""
        (native_time, _native_queries), (module_time, _module_queries) = self._measure_both()
        self.assertLessEqual(module_time - native_time, MAX_EXTRA_SECONDS)
