from unittest.mock import patch

from ..checks import b6_tracking
from .acceptance_base import AuditAcceptanceCase


class TestB6Own(AuditAcceptanceCase):
    """Requirements: FR-OPA-0021 (T-009)."""

    def _run_b6(self, tracked, updates):
        check = b6_tracking.TrackingCheck
        probe = type(self.env['perf.audit.probe'])
        with patch.object(check, 'needs', return_value=None), \
                patch.object(check, 'tracked_fields', return_value=tracked), \
                patch.object(probe, 'table_updates', return_value=updates):
            return self.findings(self.start_run(), 'B6')

    def _tracked(self, count):
        return {'res.partner': ['f%d' % number for number in range(count)]}

    def test_reported_above_both_thresholds(self):
        """FR-OPA-0021 / SC-OPA-0021-1: 14 tracked fields and 300,000 updates give one finding."""
        found = self._run_b6(self._tracked(14), {'res_partner': 300000})
        self.assertEqual(len(found), 1)
        self.assertEqual(found.model_id.model, 'res.partner')
        self.assertEqual(found.found_value, 14)

    def test_boundaries_are_not_above(self):
        """FR-OPA-0021: exactly 10 tracked fields, or exactly 100,000 updates, is not above."""
        self.assertFalse(self._run_b6(self._tracked(10), {'res_partner': 300000}))
        self.assertFalse(self._run_b6(self._tracked(14), {'res_partner': 100000}))
        self.assertEqual(len(self._run_b6(self._tracked(11), {'res_partner': 100001})), 1)
