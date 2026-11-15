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

    def test_open_issues_of_a_record_with_no_company_count_for_the_scan_company(self):
        """FR-DGQ-0031: a company-less contact's issue is counted where its score is counted."""
        rule = self.vendor_rule(severity='critical', company=False)
        self.isolate('res.partner')
        contact = self.env['res.partner'].create({'name': 'Shared', 'function': 'DGQVEND', 'company_id': False})
        scan = self.run_scan([rule], models=['res.partner'])
        domain = [('scan_id', '=', scan.id), ('company_id', '=', self.cx.id)]
        snapshot = self.env['data.quality.score.snapshot'].search(domain + [('rule_id', '=', False)])
        per_rule = self.env['data.quality.score.snapshot'].search(domain + [('rule_id', '=', rule.id)])
        self.assertTrue(self.issues_of(rule, contact))
        self.assertEqual(snapshot.issues_open_critical, 1)
        self.assertEqual(per_rule.issues_open_critical, 1)

    def test_each_company_snapshot_shows_the_open_issues_of_its_own_company(self):
        """FR-DGQ-0031: a scan of two companies keys the issue counts by each row's company."""
        rule = self.vendor_rule(severity='critical', company=False)
        self.isolate('res.partner')
        self.env['res.partner'].create([
            {'name': 'X one', 'function': 'DGQVEND', 'company_id': self.cx.id},
            {'name': 'X two', 'function': 'DGQVEND', 'company_id': self.cx.id},
            {'name': 'Y one', 'function': 'DGQVEND', 'company_id': self.cy.id}])
        scan = self.run_scan([rule], companies=[self.cx, self.cy], models=['res.partner'])
        Snapshot = self.env['data.quality.score.snapshot']
        for company, expected in ((self.cx, 2), (self.cy, 1)):
            domain = [('scan_id', '=', scan.id), ('company_id', '=', company.id)]
            self.assertEqual(Snapshot.search(domain + [('rule_id', '=', False)]).issues_open_critical, expected)
            self.assertEqual(Snapshot.search(domain + [('rule_id', '=', rule.id)]).issues_open_critical, expected)

    def test_a_scan_of_a_model_without_company_field_stores_a_snapshot(self):
        """FR-DGQ-0031: a model with no company field is scored under the scan's first company."""
        self.env['data.quality.model.config'].create({
            'model_id': self.env['ir.model']._get('res.partner.category').id, 'status': 'supported'})
        rule = self.make_rule('res.partner.category', 'required', ['parent_id'], severity='warning', company=False)
        self.env['res.partner.category'].create({'name': 'Untidy'})
        scan = self.run_scan([rule], models=['res.partner.category'])
        self.assertEqual(scan.state, 'done')
        snapshot = self.env['data.quality.score.snapshot'].search([
            ('scan_id', '=', scan.id), ('rule_id', '=', False)])
        self.assertEqual(snapshot.company_id, self.cx)
