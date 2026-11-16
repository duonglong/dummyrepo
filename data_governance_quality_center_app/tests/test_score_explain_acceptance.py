from odoo.tests import tagged

from .acceptance_m3_helpers import M3Case


@tagged('post_install', '-at_install')
class TestScoreExplainAcceptance(M3Case):
    """Acceptance tests of the explanation of a score change.

    Requirements: FR-DGQ-0035
    The question is asked of ``data.quality.dashboard.explain_change(snapshot_id, previous_id)``;
    the screen that shows the causes belongs to the client widget.
    """

    def setUp(self):
        super().setUp()
        self.env['data.quality.score.snapshot'].search([]).unlink()
        self.dashboard = self.env['data.quality.dashboard'].with_user(self.viewer)

    def snap(self, model, applicable, failed, date, **vals):
        return self.env['data.quality.score.snapshot'].create(dict({
            'model_id': self.model(model).id, 'company_id': self.cx.id, 'date': date,
            'applicable_weight': applicable, 'failed_weight': failed, 'has_score': True,
            'score': 100.0 * (1 - failed / applicable)}, **vals))

    def rule_row(self, parent, rule, applicable, failed):
        return self.snap(
            parent.model_id.model, applicable, failed, parent.date, scan_id=parent.scan_id.id,
            rule_id=rule.id, rule_version=1, severity=rule.severity)

    def test_new_critical_rule_is_the_first_cause(self):
        """SC-DGQ-0035-1: Contact 85 -> 78, the new critical rule 'Vendor needs VAT' fails on 20 contacts."""
        kept = self.make_rule('res.partner', 'required', ['name'], name='Contact needs name')
        added = self.make_rule('res.partner', 'required', ['vat'], name='Vendor needs VAT', severity='critical')
        old = self.snap('res.partner', 200, 30, '2026-11-01 10:00:00')
        self.rule_row(old, kept, 200, 30)
        new = self.snap('res.partner', 300, 66, '2026-11-08 10:00:00')
        self.rule_row(new, kept, 200, 30)
        self.rule_row(new, added, 100, 36)
        result = self.dashboard.explain_change(new.id)
        self.assertEqual((round(result['score_before']), round(result['score_after'])), (85, 78))
        first = result['causes'][0]
        self.assertEqual((first['kind'], first['rule_id'], first['rule_name']),
                         ('rule_added', added.id, 'Vendor needs VAT'))
        self.assertAlmostEqual(first['points'], -7.0)

    def test_causes_come_in_order_of_their_effect(self):
        """SC-DGQ-0035-1 (order): a small data change is listed after the bigger new-rule cause."""
        kept = self.make_rule('res.partner', 'required', ['name'])
        added = self.make_rule('res.partner', 'required', ['vat'], severity='critical')
        old = self.snap('res.partner', 200, 30, '2026-11-01 10:00:00')
        self.rule_row(old, kept, 200, 30)
        new = self.snap('res.partner', 300, 67, '2026-11-08 10:00:00')
        self.rule_row(new, kept, 200, 31)
        self.rule_row(new, added, 100, 36)
        causes = self.dashboard.explain_change(new.id)['causes']
        self.assertEqual(causes[0]['kind'], 'rule_added')
        sizes = [abs(c['points']) for c in causes]
        self.assertEqual(sizes, sorted(sizes, reverse=True))
        self.assertGreater(len(causes), 1)

    def test_fewer_failures_of_a_rule_explain_a_rise(self):
        """SC-DGQ-0035-2: Product 90 -> 95 with no rule change, 30 fewer products fail 'Product needs barcode'."""
        rule = self.make_rule('product.template', 'required', ['barcode'], name='Product needs barcode')
        old = self.snap('product.template', 300, 30, '2026-11-01 10:00:00')
        self.rule_row(old, rule, 300, 30)
        new = self.snap('product.template', 300, 15, '2026-11-08 10:00:00')
        self.rule_row(new, rule, 300, 15)
        result = self.dashboard.explain_change(new.id, previous_id=old.id)
        self.assertEqual((round(result['score_before']), round(result['score_after'])), (90, 95))
        self.assertEqual(len(result['causes']), 1)
        cause = result['causes'][0]
        self.assertEqual((cause['kind'], cause['rule_id'], cause['rule_name']),
                         ('failed_weight_changed', rule.id, 'Product needs barcode'))
        self.assertAlmostEqual(cause['points'], 5.0)
