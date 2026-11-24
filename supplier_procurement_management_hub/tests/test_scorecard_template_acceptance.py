"""Acceptance tests of T-027 (scorecard templates: metrics, weights, ranges and periods).

Requirements: FR-SPM-0082, BR-SPM-0013

Written from the specification. The template form (name, period, good and acceptable
thresholds, metric rows with a weight) is driven with Odoo's Form helper; the shipped view's
exact field order is not asserted. SC-SPM-0082-1 (the weighted score) needs the scorecard
snapshot model of T-028 and is recorded there.
"""
from odoo.exceptions import ValidationError
from odoo.tests import Form

from .scorecard_common import SpmScorecardCase


class TestScorecardTemplateAcceptance(SpmScorecardCase):
    """Requirements: FR-SPM-0082"""

    def _form_save(self, weights, period='quarter'):
        form = Form(self.env['procurement.scorecard.template'].with_user(self.manager))
        form.name = 'Template form M6'
        form.period = period
        form.good_from = 85.0
        form.acceptable_from = 60.0
        for kind, weight in weights:
            with form.metric_ids.new() as line:
                line.metric = kind
                line.weight = weight
        return form.save()

    def test_sc_0082_2_a_template_with_all_weights_zero_is_refused(self):
        """SC-SPM-0082-2: a template with all weights 0; when the manager saves it, the save
        is refused."""
        with self.assertRaises(ValidationError):
            with self.cr.savepoint():
                self._form_save([('on_time', 0.0), ('fulfillment', 0.0), ('quality', 0.0)])

    def test_fr_0082_a_template_without_metric_rows_is_refused(self):
        """FR-SPM-0082: weights total 0 also when there is no metric row."""
        with self.assertRaises(ValidationError):
            with self.cr.savepoint():
                self._template([])

    def test_fr_0082_the_smallest_positive_total_is_saved(self):
        """FR-SPM-0082 boundary: weights 0, 0 and 0.1 (just above a total of 0) are accepted
        and the period is kept."""
        template = self._form_save([('on_time', 0.1), ('fulfillment', 0.0)], period='year')
        self.assertTrue(template.id)
        self.assertEqual(template.period, 'year')
        self.assertEqual(sorted(template.metric_ids.mapped('weight')), [0.0, 0.1])

    def test_fr_0082_a_change_that_brings_the_total_to_zero_is_refused(self):
        """FR-SPM-0082: editing a saved template down to a weight total of 0 is refused too."""
        template = self._template([('on_time', 40.0), ('quality', 60.0)], period='month')
        with self.assertRaises(ValidationError):
            with self.cr.savepoint():
                template.metric_ids.write({'weight': 0.0})
                self.env.flush_all()

    def test_fr_0082_month_quarter_and_year_are_the_periods(self):
        """FR-SPM-0082: the three evaluation periods are accepted."""
        for period in ('month', 'quarter', 'year'):
            self.assertEqual(self._template([('on_time', 1.0)], period=period).period, period)
