from odoo.tests import tagged

from ..services import scoring
from .acceptance_m3_helpers import M3Case


@tagged('post_install', '-at_install')
class TestScoring(M3Case):
    """Score formula and the weights of a scan's snapshot.

    Requirements: BR-DGQ-0001, BR-DGQ-0002, FR-DGQ-0031
    """

    def test_score_is_none_when_nothing_applies(self):
        """BR-DGQ-0001: no applicable weight gives no score, not 100."""
        self.assertIsNone(scoring.score(0, 0))

    def test_score_is_the_weighted_share(self):
        """BR-DGQ-0001: 10 warning checks, 2 failing, give 80."""
        self.assertEqual(scoring.score(30, 6), 80.0)

    def test_weights_come_from_the_settings(self):
        """BR-DGQ-0001: a weight is a setting; defaults are 1, 3 and 10."""
        self.assertEqual(scoring.severity_weights(self.env), {'info': 1, 'warning': 3, 'critical': 10})
        self.env['ir.config_parameter'].sudo().set_int('data_quality.weight_warning', 5)
        self.assertEqual(scoring.severity_weights(self.env)['warning'], 5)

    def test_snapshot_keeps_the_weights_used(self):
        """FR-DGQ-0031: the weights of the moment are stored with the snapshot."""
        rule = self.make_rule('res.partner', 'required', ['ref'], severity='warning')
        self.isolate('res.partner')
        self.env['res.partner'].create({'name': 'Weights', 'company_id': self.cx.id})
        self.env['ir.config_parameter'].sudo().set_int('data_quality.weight_warning', 5)
        scan = self.run_scan([rule], models=['res.partner'])
        snap = self.env['data.quality.score.snapshot'].search([
            ('scan_id', '=', scan.id), ('rule_id', '=', False), ('company_id', '=', self.cx.id)])
        self.assertEqual(snap.weights_used['warning'], 5)
        self.assertEqual((snap.applicable_weight, snap.failed_weight), (5, 5))
