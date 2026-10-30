import time
from datetime import datetime

from odoo import Command
from odoo.tests.common import tagged

from .common import WeekendMultiplierCase

LINES = 100
MAX_EXTRA_SECONDS = 0.05
MAX_EXTRA_QUERIES = 2


@tagged('post_install', '-at_install')
class TestWeekendPerformance(WeekendMultiplierCase):
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
        module_class = next(
            klass for klass in type(order).__mro__
            if klass.__module__.startswith('odoo.addons.loyalty_weekend_multiplier')
            and '_program_check_compute_points' in vars(klass))
        return super(module_class, order)._program_check_compute_points(programs)

    def test_weekend_computation_overhead(self):
        """Requirements: NFR-PERF-001 - at most 50 ms and 2 queries more."""
        order = self._make_order(datetime(2026, 10, 24, 10, 0))  # Saturday
        programs = self.program
        # warm the caches so both runs read the same data
        order._program_check_compute_points(programs)
        self._native_compute(order, programs)

        queries = []
        for func in (lambda: self._native_compute(order, programs),
                     lambda: order._program_check_compute_points(programs)):
            before = self.cr.sql_log_count
            elapsed = self._measure_plain(func)
            queries.append((elapsed, self.cr.sql_log_count - before))
        (native_time, native_queries), (module_time, module_queries) = queries
        self.assertLessEqual(module_queries - native_queries, MAX_EXTRA_QUERIES)
        self.assertLessEqual(module_time - native_time, MAX_EXTRA_SECONDS)

    def _measure_plain(self, func):
        started = time.perf_counter()
        func()
        return time.perf_counter() - started
