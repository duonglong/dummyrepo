from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from odoo import fields
from odoo.tests.common import tagged

from .common import PerfAuditCase

PROBE_RESET = 'odoo.addons.perf_audit.models.perf_audit_probe.PerfAuditProbe.stats_reset'


@tagged('post_install', '-at_install')
class TestStatsReset(PerfAuditCase):
    """Requirements: FR-OPA-0004."""

    def _run_with_reset(self, age_days, source='reset'):
        reset = datetime.now(timezone.utc) - timedelta(days=age_days)
        with patch(PROBE_RESET, return_value=(reset, source)):
            self.env['perf.audit.run'].with_user(self.audit_admin).action_start_audit()
        return self.env['perf.audit.run'].search([], order='id desc', limit=1)

    def test_reset_two_days_ago_is_weak(self):
        """SC-OPA-0004-1: a reset 2 days ago is stored and marks weak evidence."""
        run = self._run_with_reset(2)
        self.assertAlmostEqual(
            run.stats_reset_date, fields.Datetime.now() - timedelta(days=2), delta=timedelta(minutes=2))
        self.assertTrue(run.weak_evidence)

    def test_reset_thirty_days_ago_is_not_weak(self):
        """SC-OPA-0004-2: a reset 30 days ago is not weak evidence."""
        self.assertFalse(self._run_with_reset(30).weak_evidence)

    def test_boundary_of_seven_days(self):
        """Just inside the 7-day threshold is weak, just outside is not."""
        self.assertTrue(self._run_with_reset(6.9).weak_evidence)
        self.assertFalse(self._run_with_reset(7.1).weak_evidence)

    def test_server_start_fallback(self):
        """Without a reset time the probe gives the server start with its source."""
        self.assertEqual(self._run_with_reset(1, 'server_start').stats_reset_source, 'server_start')

    def test_probe_reads_a_reset_or_server_start(self):
        """The probe returns a time and the source it came from."""
        reset_date, source = self.env['perf.audit.probe'].stats_reset()
        self.assertTrue(reset_date)
        self.assertIn(source, ('reset', 'server_start'))

    def test_probe_entry_is_private(self):
        """The probe's statistics reads cannot be called remotely."""
        self.assertTrue(getattr(type(self.env['perf.audit.probe']).stats_reset, '_api_private', False))
