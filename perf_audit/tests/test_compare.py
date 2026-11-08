"""Own tests of T-012: comparison closure, repeated closure and fixed rows.

Requirements: BR-OPA-0003, FR-OPA-0030
"""
from datetime import timedelta
from unittest import mock

from odoo.tests.common import tagged

from .acceptance_base import CHECK_CODES, AuditAcceptanceCase
from .test_compare_acceptance import _Stub, _finding


@tagged('post_install', '-at_install')
class TestCompare(AuditAcceptanceCase):
    """Requirements: FR-OPA-0030 (repeated closure, fixed rows, regression after a fix)."""

    A1 = 'A1:sale_order'
    A5 = 'A5:x_line'

    def execute(self, hours_ago, stubs=None):
        """Execute a run started `hours_ago` hours ago with stub checks and return it."""
        registry = {code: _Stub() for code in CHECK_CODES}
        registry.update(stubs or {})
        run = self.env['perf.audit.run'].sudo().create({
            'user_id': self.audit_admin.id,
            'start_date': self.now() - timedelta(hours=hours_ago),
        })
        with mock.patch.object(type(run), '_check_registry', lambda self: registry):
            run._execute()
        self.env.flush_all()
        return run

    def mark(self, run, identity_key):
        return run.finding_ids.filtered(lambda f: f.identity_key == identity_key).comparison_state

    def test_closing_twice_gives_the_same_marks(self):
        """FR-OPA-0030: comparing a run again rebuilds its fixed rows and keeps the counts."""
        self.execute(2, {'A5': _Stub([_finding(self.A5)])})
        run = self.execute(1, {'A1': _Stub([_finding(self.A1)])})
        counts = (run.finding_count, run.new_count, run.fixed_count, run.regressed_count)
        run._compare_with_earlier_runs()
        self.assertEqual(
            (run.finding_count, run.new_count, run.fixed_count, run.regressed_count), counts)
        self.assertEqual(len(run.finding_ids), 2)

    def test_fixed_row_offers_no_fix_and_stays_out_of_the_script(self):
        """FR-OPA-0030: a fixed row has nothing to apply and no statement in the fix script."""
        fix = {'fix_kind': 'analyze', 'fix_params': {'table': 'x_line'}}
        self.execute(2, {'A5': _Stub([_finding(self.A5, **fix)])})
        run = self.execute(1)
        fixed = run.finding_ids
        self.assertEqual(fixed.comparison_state, 'fixed')
        self.assertFalse(fixed.can_apply)
        self.assertNotIn('ANALYZE', bytes(run.fix_script).decode())

    def test_fixed_finding_is_not_reported_again_next_run(self):
        """FR-OPA-0030: a finding listed fixed is gone, a later reappearance is regressed."""
        self.execute(3, {'A1': _Stub([_finding(self.A1)])})
        self.execute(2)
        third = self.execute(1)
        self.assertFalse(third.finding_ids)
        run = self.execute(0, {'A1': _Stub([_finding(self.A1)])})
        self.assertEqual(self.mark(run, self.A1), 'regressed')
