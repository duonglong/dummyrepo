import base64
import json

from odoo.tests.common import tagged

from .acceptance_base import AcceptanceBase


@tagged('post_install', '-at_install')
class TestImportCancelAcceptance(AcceptanceBase):
    """Acceptance tests of T-006: cancelling the confirmation returns to the differences list."""

    def setUp(self):
        super().setUp()
        self.env['config.snapshot.item'].sudo().search([]).unlink()
        self.log = self.env['config.snapshot.log'].sudo()
        self.params = self.env['ir.config_parameter'].sudo()

    def _confirmed(self):
        """1 modified (m.can) and 1 added (n.can), confirmation shown."""
        param = self._param('m.can', 'a')
        self.env['config.snapshot.item'].sudo().create({'parameter_id': param.id})
        raw = json.dumps({'format_version': 1, 'exported_on': '2026-01-01', 'settings': [
            {'key': 'm.can', 'value': 'b', 'sensitive': False},
            {'key': 'n.can', 'value': 'c', 'sensitive': False}]}).encode()
        wiz = self.env['config.snapshot.import'].with_user(self.admin_user).create(
            {'file': base64.b64encode(raw), 'file_name': 'prod.json'})
        wiz.action_compare()
        wiz.action_confirm()
        return wiz

    def _log_count(self):
        self.env.flush_all()
        return self.log.search_count([])

    def test_cancel_returns_to_difference_list(self):
        """Requirements: FR-TCM-008, SC-TCM-008-2: cancel shows the same 2 lines again."""
        wiz = self._confirmed()
        lines_before = wiz.line_ids
        self.assertEqual(wiz.state, 'confirm')
        action = wiz.action_cancel()
        self.assertEqual(wiz.state, 'review')
        self.assertEqual(wiz.line_ids, lines_before)
        self.assertEqual(len(wiz.line_ids), 2)
        self.assertEqual(wiz.difference_count, 2)
        self.assertEqual(action.get('type'), 'ir.actions.act_window')
        self.assertEqual(action.get('res_model'), 'config.snapshot.import')
        self.assertEqual(action.get('res_id'), wiz.id)
        self.assertEqual(action.get('target'), 'new')

    def test_cancel_changes_and_logs_nothing(self):
        """Requirements: FR-TCM-008, SC-TCM-008-2: every setting keeps its value, no log entry."""
        wiz = self._confirmed()
        logs = self._log_count()
        wiz.action_cancel()
        self.env.invalidate_all()
        self.assertEqual(self.params.search([('key', '=', 'm.can')]).value, 'a')
        self.assertFalse(self.params.search([('key', '=', 'n.can')]))
        self.assertEqual(self._log_count(), logs)

    def test_apply_offered_again_after_cancel(self):
        """Requirements: FR-TCM-008, SC-TCM-008-2: apply is offered again and works without reloading."""
        wiz = self._confirmed()
        wiz.action_cancel()
        # The Apply button (action_confirm) is offered in state review with differences.
        action = wiz.action_confirm()
        self.assertEqual(wiz.state, 'confirm')
        self.assertEqual(action.get('res_id'), wiz.id)
        wiz.action_apply()
        self.env.invalidate_all()
        self.assertEqual(self.params.search([('key', '=', 'm.can')]).value, 'b')
        self.assertEqual(self.params.search([('key', '=', 'n.can')]).value, 'c')

    def test_cancel_twice_cycle(self):
        """Requirements: FR-TCM-008: the cycle apply, cancel, apply, cancel is repeatable."""
        wiz = self._confirmed()
        wiz.action_cancel()
        wiz.action_confirm()
        wiz.action_cancel()
        self.assertEqual(wiz.state, 'review')
        self.assertEqual(len(wiz.line_ids), 2)
