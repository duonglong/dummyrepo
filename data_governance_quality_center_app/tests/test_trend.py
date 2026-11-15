from unittest.mock import patch

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from .acceptance_m3_helpers import M3Case


@tagged('post_install', '-at_install')
class TestTrend(M3Case):
    """The score trend, the explanation of a change, the drill-down and the widget layout service.

    Requirements: FR-DGQ-0034, FR-DGQ-0035, FR-DGQ-0036, FR-DGQ-0037
    """

    def setUp(self):
        super().setUp()
        self.env['data.quality.score.snapshot'].search([]).unlink()
        self.dashboard = self.env['data.quality.dashboard'].with_user(self.viewer)

    def snap(self, applicable, failed, date, model='res.partner', company=None, **vals):
        return self.env['data.quality.score.snapshot'].create(dict({
            'model_id': self.model(model).id, 'company_id': (company or self.cx).id, 'date': date,
            'applicable_weight': applicable, 'failed_weight': failed, 'has_score': bool(applicable)}, **vals))

    def rule_row(self, parent, rule, applicable, failed, version=1):
        return self.snap(
            applicable, failed, parent.date, scan_id=parent.scan_id.id, rule_id=rule.id, rule_version=version,
            severity=rule.severity)

    def test_weekly_trend_has_a_gap_where_no_scan_ran(self):
        """SC-DGQ-0034-1: weeks 44 and 46 have points, week 45 has none."""
        self.snap(100, 30, '2026-10-28 10:00:00')  # week 44
        self.snap(100, 18, '2026-11-11 10:00:00')  # week 46
        points = self.dashboard.get_trend(period='week')['points']
        self.assertEqual([round(p['score'], 1) if p['score'] is not None else None for p in points], [70.0, None, 82.0])

    def test_the_last_snapshot_of_the_period_counts_and_the_model_filter_applies(self):
        """FR-DGQ-0034: the later of two scans of a week is used, and another model is left out."""
        self.snap(100, 50, '2026-11-09 08:00:00')
        self.snap(100, 10, '2026-11-10 08:00:00')
        self.snap(100, 100, '2026-11-10 08:00:00', model='product.template')
        points = self.dashboard.get_trend(period='week', model_id=self.model('res.partner').id)['points']
        self.assertEqual([p['score'] for p in points], [90.0])
        self.assertEqual(len(self.dashboard.get_trend(period='month')['points']), 1)

    def test_a_wrong_period_is_refused(self):
        """FR-DGQ-0034: only day, week or month."""
        with self.assertRaises(UserError):
            self.dashboard.get_trend(period='year')

    def explain_pair(self):
        old = self.snap(200, 30, '2026-11-01 10:00:00')
        stays = self.make_rule('res.partner', 'required', ['name'])
        self.rule_row(old, stays, 200, 30)
        new = self.snap(300, 66, '2026-11-08 10:00:00')
        self.rule_row(new, stays, 200, 30)
        return old, new, stays

    def test_a_new_rule_is_the_first_cause_with_its_points(self):
        """SC-DGQ-0035-1: the added rule that fails on records is listed first."""
        old, new, _stays = self.explain_pair()
        added = self.make_rule('res.partner', 'required', ['email'], severity='critical')
        self.rule_row(new, added, 100, 36)
        result = self.dashboard.explain_change(new.id)
        first = result['causes'][0]
        self.assertEqual((first['kind'], first['rule_id']), ('rule_added', added.id))
        self.assertAlmostEqual(first['points'], 100 * (1 - 66 / 300) - 100 * (1 - 30 / 200))
        self.assertAlmostEqual(sum(c['points'] for c in result['causes']), 100 * (1 - 66 / 300) - 85)

    def test_fewer_failures_are_a_cause_of_a_rise(self):
        """SC-DGQ-0035-2: the rule fails on fewer records and nothing else changed."""
        old = self.snap(100, 10, '2026-11-01 10:00:00')
        rule = self.make_rule('res.partner', 'required', ['name'])
        self.rule_row(old, rule, 100, 10)
        new = self.snap(100, 5, '2026-11-08 10:00:00')
        self.rule_row(new, rule, 100, 5)
        causes = self.dashboard.explain_change(new.id, previous_id=old.id)['causes']
        self.assertEqual([(c['kind'], c['rule_id']) for c in causes], [('failed_weight_changed', rule.id)])
        self.assertAlmostEqual(causes[0]['points'], 5.0)

    def test_data_changes_are_apart_from_rule_changes(self):
        """FR-DGQ-0035: new records, a new version and a switched off rule are three causes of their own."""
        old = self.snap(300, 60, '2026-11-01 10:00:00')
        grows = self.make_rule('res.partner', 'required', ['name'])
        versioned = self.make_rule('res.partner', 'required', ['email'])
        off = self.make_rule('res.partner', 'required', ['phone'])
        for rule, applicable, failed in ((grows, 100, 20), (versioned, 100, 20), (off, 100, 20)):
            self.rule_row(old, rule, applicable, failed)
        off.active = False
        new = self.snap(300, 60, '2026-11-08 10:00:00')
        self.rule_row(new, grows, 200, 20)
        self.rule_row(new, versioned, 100, 40, version=2)
        kinds = {c['kind']: c['rule_id'] for c in self.dashboard.explain_change(new.id)['causes']}
        self.assertEqual(kinds, {
            'applicable_weight_changed': grows.id, 'rule_version_changed': versioned.id,
            'rule_switched_off': off.id})

    def test_there_must_be_an_earlier_snapshot(self):
        """FR-DGQ-0035: the first snapshot has nothing to compare with."""
        first = self.snap(100, 10, '2026-11-01 10:00:00')
        with self.assertRaises(UserError):
            self.dashboard.explain_change(first.id)

    def test_drill_down_lists_the_open_critical_issues_of_the_chosen_company(self):
        """SC-DGQ-0036-1: the critical figure of company X opens its 3 critical open issues only."""
        rule = self.make_rule('res.partner', 'required', ['name'], severity='critical')
        partners = self.env['res.partner'].create([{'name': 'P%s' % i} for i in range(3)])
        Issue = self.env['data.quality.issue']
        for partner in partners:
            Issue.create({'rule_id': rule.id, 'res_model': 'res.partner', 'res_id': partner.id,
                          'severity': 'critical', 'company_id': self.cx.id})
        other = self.make_rule('res.partner', 'required', ['email'], severity='critical')
        Issue.create({'rule_id': other.id, 'res_model': 'res.partner', 'res_id': partners[0].id,
                      'severity': 'critical', 'company_id': self.cy.id})
        action = self.dashboard.drill_down('critical_issues', company_ids=[self.cx.id])
        issues = Issue.search(action['domain'])
        self.assertEqual((action['res_model'], len(issues)), ('data.quality.issue', 3))
        self.assertEqual(issues.company_id, self.cx)

    def test_drill_down_of_a_model_opens_its_open_issues_and_of_error_records_the_records(self):
        """SC-DGQ-0036-2: a model's score opens its open issues; 'records with errors' opens the records."""
        rule = self.make_rule('res.partner', 'required', ['name'], severity='critical')
        partner = self.env['res.partner'].create({'name': 'Bad'})
        Issue = self.env['data.quality.issue']
        Issue.create({'rule_id': rule.id, 'res_model': 'res.partner', 'res_id': partner.id,
                      'severity': 'critical', 'company_id': self.cx.id})
        model_id = self.model('res.partner').id
        action = self.dashboard.drill_down('model', model_id=model_id)
        self.assertEqual(Issue.search(action['domain']).mapped('res_model'), ['res.partner'])
        records = self.dashboard.drill_down('records_error', model_id=model_id)
        self.assertEqual((records['res_model'], records['domain']), ('res.partner', [('id', 'in', partner.ids)]))

    def test_layout_service_hides_for_one_user_and_resets(self):
        """SC-DGQ-0037-1/2: Bob hides a widget and Ann does not see it hidden; a reset restores the default."""
        bob = self.env['data.quality.dashboard'].with_user(self.viewer)
        ann = self.env['data.quality.dashboard'].with_user(self.user('viewer', login='dgq_t15_ann', company=self.cx))
        default = [item['widget'] for item in ann.get_layout()]
        shown = [{'widget': widget, 'visible': widget != 'recent'} for widget in default]
        bob.save_layout(shown)
        self.assertFalse({item['widget']: item['visible'] for item in bob.get_layout()}['recent'])
        self.assertTrue({item['widget']: item['visible'] for item in ann.get_layout()}['recent'])
        bob.save_layout(list(reversed(shown)))
        self.assertEqual([item['widget'] for item in bob.get_layout()], list(reversed(default)))
        self.assertEqual([item['widget'] for item in bob.reset_layout()], default)
        self.assertTrue(all(item['visible'] for item in bob.get_layout()))

    def test_a_person_cannot_create_a_line_for_another_person(self):
        """FR-DGQ-0037: a line is always the creator's own."""
        with self.assertRaises(AccessError):
            self.env['data.quality.dashboard.layout'].with_user(self.viewer).create(
                {'user_id': self.operator.id, 'widget': 'trend'})

    def test_an_unknown_widget_is_refused(self):
        """FR-DGQ-0037: only the dashboard's widgets have a place."""
        with self.assertRaises(UserError):
            self.dashboard.save_layout([{'widget': 'nonsense', 'visible': True}])

    def test_a_default_user_cannot_put_a_line_into_another_persons_layout(self):
        """FR-DGQ-0037: a context default for the owner is judged like a value."""
        with self.assertRaises(AccessError):
            self.env['data.quality.dashboard.layout'].with_user(self.viewer).with_context(
                default_user_id=self.operator.id).create({'widget': 'recent', 'visible': False})

    def test_a_line_cannot_be_handed_to_another_person(self):
        """FR-DGQ-0037: writing the owner of one's own line is refused."""
        line = self.env['data.quality.dashboard.layout'].with_user(self.viewer).create({'widget': 'recent'})
        with self.assertRaises(AccessError):
            line.write({'user_id': self.operator.id})
        self.assertEqual(line.user_id, self.viewer)

    def test_the_trend_carries_models_scanned_earlier_like_the_dashboard(self):
        """FR-DGQ-0034: a week with a scan of one model only scores the same as the dashboard on its last day."""
        self.snap(100, 30, '2026-10-28 10:00:00')
        self.snap(100, 5, '2026-10-29 10:00:00', model='product.template')
        self.snap(100, 20, '2026-11-04 10:00:00')  # week 45: Contact only
        points = self.dashboard.get_trend(period='week')['points']
        figures = self.dashboard.get_figures(date='2026-11-08')
        self.assertEqual(len(points), 2)
        self.assertAlmostEqual(points[0]['score'], 100 * (1 - 35 / 200))
        self.assertAlmostEqual(points[1]['score'], figures['score'])
        self.assertAlmostEqual(points[1]['score'], 100 * (1 - 25 / 200))
        self.assertFalse(points[1]['snapshot_id'])

    def test_the_trend_of_a_company_and_model_has_a_snapshot_to_explain(self):
        """FR-DGQ-0035: scoped to one company and model a point names its one snapshot."""
        first = self.snap(100, 30, '2026-10-28 10:00:00')
        self.snap(100, 5, '2026-10-29 10:00:00', model='product.template')
        points = self.dashboard.get_trend(
            company_ids=[self.cx.id], model_id=self.model('res.partner').id, period='week')['points']
        self.assertEqual([p['snapshot_id'] for p in points], [first.id])

    def test_a_cut_list_of_records_says_so(self):
        """FR-DGQ-0036: the list of records behind a figure names its limit when it is cut."""
        rule = self.make_rule('res.partner', 'required', ['name'], severity='critical')
        partners = self.env['res.partner'].create([{'name': 'P%s' % i} for i in range(3)])
        for partner in partners:
            self.env['data.quality.issue'].create({
                'rule_id': rule.id, 'res_model': 'res.partner', 'res_id': partner.id,
                'severity': 'critical', 'company_id': self.cx.id})
        model_id = self.model('res.partner').id
        with patch('odoo.addons.data_governance_quality_center_app.models.dashboard.DRILL_DOWN_RECORD_LIMIT', 2):
            action = self.dashboard.drill_down('records_error', model_id=model_id)
        self.assertEqual(action['domain'], [('id', 'in', sorted(partners.ids)[:2])])
        self.assertIn('first 2', action['name'])

    def test_a_point_is_explained_against_the_point_before_it(self):
        """FR-DGQ-0035: with several scans in a week, a point names the last snapshot of the earlier point."""
        week_44 = self.snap(100, 30, '2026-10-29 10:00:00', score=70.0)
        self.snap(100, 25, '2026-11-02 10:00:00', score=75.0)
        week_45 = self.snap(100, 20, '2026-11-05 10:00:00', score=80.0)
        points = self.dashboard.get_trend(
            company_ids=[self.cx.id], model_id=self.model('res.partner').id, period='week')['points']
        self.assertEqual([p['previous_snapshot_id'] for p in points], [False, week_44.id])
        result = self.dashboard.explain_change(points[1]['snapshot_id'], points[1]['previous_snapshot_id'])
        self.assertEqual(points[1]['snapshot_id'], week_45.id)
        self.assertAlmostEqual(result['score_before'], 70.0)
        self.assertAlmostEqual(result['score_after'], 80.0)
