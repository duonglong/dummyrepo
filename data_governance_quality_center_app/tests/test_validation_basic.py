from datetime import date

from odoo import fields
from odoo.addons.data_governance_quality_center_app.services import validation
from odoo.addons.data_governance_quality_center_app.services.validation import (
    FAIL,
    NOT_APPLICABLE,
    PASS,
)

from .acceptance_helpers import AcceptanceCase

# The moment of the scans of these tests, in UTC: 13 Nov in Sydney, still 12 Nov in Los Angeles.
SCAN_MOMENT = fields.Datetime.to_datetime('2026-11-12 18:00:00')
SCAN_DAY = date(2026, 11, 12)


class TestValidationBasic(AcceptanceCase):
    """The validation service called directly, without a scan.

    Requirements: FR-DGQ-0002, FR-DGQ-0003, FR-DGQ-0004, FR-DGQ-0005
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.ensure_field('res.partner', 'x_dgq_seen_at', 'datetime', 'DGQ Seen At')
        cls.cx.partner_id.tz = 'Australia/Sydney'
        cls.cy.partner_id.tz = 'America/Los_Angeles'

    def _outcomes(self, rule, *records, now=SCAN_MOMENT):
        records = records[0].browse([r.id for r in records])
        return validation.check(rule, records, now=now)

    def _status(self, rule, record, now=SCAN_MOMENT):
        return self._outcomes(rule, record, now=now)[(record.id, rule.id)].status

    def _date_rule(self, condition, field='x_dgq_born_on', **vals):
        return self.make_rule('res.partner', 'date', [field], company=False, date_condition=condition, **vals)

    def _explain(self, outcome):
        return validation.explain(self.env, outcome.code, outcome.params)

    # ---- required --------------------------------------------------------------

    def test_required_reads_empty_values(self):
        """FR-DGQ-0002: spaces and an empty list are empty; the failing fields are named."""
        rule = self.make_rule('res.partner', 'required', ['email', 'category_id'])
        tag = self.env['res.partner.category'].create({'name': 'DGQ tag'})
        spaces = self.partner('Spaces', email='  ', category_id=[(6, 0, [tag.id])])
        no_tag = self.partner('No tag', email='a@b.co')
        filled = self.partner('Filled', email='a@b.co', category_id=[(6, 0, [tag.id])])
        outcomes = self._outcomes(rule, spaces, no_tag, filled)
        self.assertEqual(outcomes[(spaces.id, rule.id)].status, FAIL)
        self.assertEqual(outcomes[(spaces.id, rule.id)].params['fields'], ['email'])
        self.assertEqual(outcomes[(no_tag.id, rule.id)].params['fields'], ['category_id'])
        self.assertEqual(outcomes[(filled.id, rule.id)].status, PASS)
        self.assertIn('Email', self._explain(outcomes[(spaces.id, rule.id)]))

    def test_required_zero_and_unset_choice(self):
        """FR-DGQ-0002: a price of 0 is a value; an unset choice is empty."""
        price_rule = self.make_rule('product.template', 'required', ['list_price'])
        sample = self.product('Sample', list_price=0)
        self.assertEqual(self._status(price_rule, sample), PASS)
        choice_rule = self.make_rule('res.partner', 'required', ['type'])
        unset = self.partner('Unset')
        unset.type = False
        self.assertEqual(self._status(choice_rule, unset), FAIL)

    def test_required_emptied_rich_text_is_empty(self):
        """FR-DGQ-0002: a rich text left with only the editor's empty markup is empty."""
        rule = self.make_rule('res.partner', 'required', ['comment'])
        emptied = self.partner('Emptied', comment='<p><br></p>')
        written = self.partner('Written', comment='<p>Key account</p>')
        outcomes = self._outcomes(rule, emptied, written)
        self.assertEqual(outcomes[(emptied.id, rule.id)].status, FAIL)
        self.assertEqual(outcomes[(written.id, rule.id)].status, PASS)

    # ---- pattern ---------------------------------------------------------------

    def test_pattern_presets_and_empty(self):
        """FR-DGQ-0003: a preset checks the whole value; an empty value is not applicable."""
        rule = self.make_rule('res.partner', 'pattern', ['email'], pattern_preset='email')
        good = self.partner('Good', email='good@example.com')
        bad = self.partner('Bad', email='good@example.com extra')
        empty = self.partner('Empty')
        outcomes = self._outcomes(rule, good, bad, empty)
        self.assertEqual(outcomes[(good.id, rule.id)].status, PASS)
        self.assertEqual(outcomes[(bad.id, rule.id)].status, FAIL)
        self.assertEqual(outcomes[(empty.id, rule.id)].status, NOT_APPLICABLE)

    def test_pattern_explanation_quotes_value_and_format(self):
        """FR-DGQ-0003: the explanation quotes the value and the expected format."""
        rule = self.make_rule('product.template', 'pattern', ['default_code'], pattern=r'[A-Z]{3}-[0-9]{4}')
        good = self.product('Good', default_code='ABC-1234')
        bad = self.product('Bad', default_code='abc-12')
        outcomes = self._outcomes(rule, good, bad)
        self.assertEqual(outcomes[(good.id, rule.id)].status, PASS)
        sentence = self._explain(outcomes[(bad.id, rule.id)])
        self.assertIn('abc-12', sentence)
        self.assertIn('[A-Z]{3}-[0-9]{4}', sentence)

    # ---- range -----------------------------------------------------------------

    def test_range_bounds_are_inside(self):
        """FR-DGQ-0004: at, just below and just above each bound of a 0..10000 range."""
        rule = self.make_rule(
            'product.template', 'range', ['x_dgq_cost'], has_min=True, min_value=0, has_max=True, max_value=10000)
        rows = [(-0.01, 'range_below'), (0, None), (10000, None), (10000.01, 'range_above'), (12000, 'range_above')]
        for cost, code in rows:
            with self.subTest(cost=cost):
                product = self.product(f'Cost {cost}', x_dgq_cost=cost)
                outcome = self._outcomes(rule, product)[(product.id, rule.id)]
                self.assertEqual(outcome.status, FAIL if code else PASS)
                self.assertEqual(outcome.code, code)

    def test_range_with_one_bound(self):
        """FR-DGQ-0004: a floor alone and a ceiling alone."""
        floor = self.make_rule('product.template', 'range', ['x_dgq_cost'], has_min=True, min_value=5)
        ceiling = self.make_rule('product.template', 'range', ['x_dgq_cost'], has_max=True, max_value=5)
        low = self.product('Low', x_dgq_cost=-1000)
        high = self.product('High', x_dgq_cost=1000000)
        self.assertEqual(self._status(floor, low), FAIL)
        self.assertEqual(self._status(floor, high), PASS)
        self.assertEqual(self._status(ceiling, low), PASS)
        self.assertEqual(self._status(ceiling, high), FAIL)

    def test_range_explanation_is_rendered(self):
        """FR-DGQ-0004: the code and parameters render a sentence with the field label."""
        rule = self.make_rule('product.template', 'range', ['x_dgq_cost'], has_max=True, max_value=10)
        high = self.product('High', x_dgq_cost=12000)
        outcome = self._outcomes(rule, high)[(high.id, rule.id)]
        self.assertEqual(outcome.code, 'range_above')
        sentence = self._explain(outcome)
        self.assertIn('DGQ Cost', sentence)
        self.assertIn('12000', sentence)

    # ---- date ------------------------------------------------------------------

    def test_date_conditions_at_their_bounds(self):
        """FR-DGQ-0005: each condition at, just inside and just outside its limit (a company in UTC)."""
        utc_company = self.env['res.company'].create({'name': 'DGQ UTC'})
        utc_company.partner_id.tz = 'UTC'
        rows = [
            ('not_future', {}, 0, PASS), ('not_future', {}, 1, FAIL),
            ('not_past', {}, 0, PASS), ('not_past', {}, -1, FAIL),
            ('not_older', {'date_days': 10}, -10, PASS), ('not_older', {'date_days': 10}, -11, FAIL),
            ('not_ahead', {'date_days': 10}, 10, PASS), ('not_ahead', {'date_days': 10}, 11, FAIL),
            ('between', {'date_from': date(2026, 11, 1), 'date_to': date(2026, 11, 30)}, -11, PASS),
            ('between', {'date_from': date(2026, 11, 1), 'date_to': date(2026, 11, 30)}, -12, FAIL),
            ('between', {'date_from': date(2026, 11, 1), 'date_to': date(2026, 11, 30)}, 18, PASS),
            ('between', {'date_from': date(2026, 11, 1), 'date_to': date(2026, 11, 30)}, 19, FAIL),
        ]
        for condition, vals, offset, status in rows:
            with self.subTest(condition=condition, offset=offset):
                rule = self._date_rule(condition, **vals)
                day = date.fromordinal(SCAN_DAY.toordinal() + offset)
                record = self.partner(f'Born {day}', company=utc_company, x_dgq_born_on=day)
                self.assertEqual(self._status(rule, record), status)

    def test_date_empty_is_not_applicable(self):
        """FR-DGQ-0005: no date leaves the record to the required field rules."""
        rule = self._date_rule('not_future')
        self.assertEqual(self._status(rule, self.partner('None')), NOT_APPLICABLE)

    def test_date_explanation_gives_date_and_limit(self):
        """SC-DGQ-0005-2: the explanation gives the date and the 365-day limit."""
        rule = self._date_rule('not_older', date_days=365)
        old = self.partner('Old', x_dgq_born_on=date(2025, 11, 1))
        outcome = self._outcomes(rule, old)[(old.id, rule.id)]
        self.assertEqual(outcome.status, FAIL)
        sentence = self._explain(outcome)
        self.assertIn('2025-11-01', sentence)
        self.assertIn('365', sentence)

    def test_day_of_the_scan_is_taken_in_each_record_company_zone(self):
        """FR-DGQ-0005: one batch, two companies far apart; each record is measured from its own company's day.

        The check runs in the Sydney company, where it is already 13 Nov; in Los Angeles it is 12 Nov.
        """
        sydney, los_angeles = self.cx, self.cy
        future = self._date_rule('not_future')
        past = self._date_rule('not_past')
        rows = [
            (future, sydney, date(2026, 11, 13), PASS),
            (future, los_angeles, date(2026, 11, 13), FAIL),
            (past, sydney, date(2026, 11, 12), FAIL),
            (past, los_angeles, date(2026, 11, 12), PASS),
        ]
        records = [self.partner(f'P{index}', company=company, x_dgq_born_on=day)
                   for index, (_rule, company, day, _status) in enumerate(rows)]
        batch = self.env['res.partner'].with_company(sydney).browse([r.id for r in records])
        for (rule, company, day, status), record in zip(rows, records):
            with self.subTest(rule=rule.date_condition, company=company.name):
                outcome = validation.check(rule, batch, now=SCAN_MOMENT)[(record.id, rule.id)]
                self.assertEqual(outcome.status, status)

    def test_datetime_value_is_read_in_the_record_company_zone(self):
        """FR-DGQ-0005: 09:00 UTC on 13 Nov is 13 Nov in both zones, today in Sydney but tomorrow in Los Angeles."""
        rule = self._date_rule('not_future', field='x_dgq_seen_at')
        seen = fields.Datetime.to_datetime('2026-11-13 09:00:00')
        sydney = self.partner('Sydney', company=self.cx, x_dgq_seen_at=seen)
        los_angeles = self.partner('Los Angeles', company=self.cy, x_dgq_seen_at=seen)
        batch = self.env['res.partner'].with_company(self.cx).browse([sydney.id, los_angeles.id])
        outcomes = validation.check(rule, batch, now=SCAN_MOMENT)
        self.assertEqual(outcomes[(sydney.id, rule.id)].status, PASS)
        self.assertEqual(outcomes[(los_angeles.id, rule.id)].status, FAIL)
        self.assertEqual(outcomes[(los_angeles.id, rule.id)].params['value'], '2026-11-13')

    # ---- hidden values and stale explanations ------------------------------------

    def test_value_of_a_sensitive_field_is_never_in_the_parameters(self):
        """ADR-DGQ-005: a field marked sensitive after the rule was saved: the value is hidden, the sentence still reads."""
        pattern = self.make_rule('product.template', 'pattern', ['default_code'], pattern=r'[A-Z]{3}')
        cost = self.make_rule('product.template', 'range', ['x_dgq_cost'], has_max=True, max_value=10)
        start = self.make_rule('product.template', 'date', ['x_dgq_start'], company=False,
                               date_condition='not_future')
        product = self.product('Secret', default_code='secret-1', x_dgq_cost=99, x_dgq_start=date(2030, 1, 1))
        for name in ('default_code', 'x_dgq_cost', 'x_dgq_start'):
            self.env['data.quality.field.config'].create({
                'field_id': self.fld('product.template', name).id, 'status': 'sensitive'})
        for rule, secret in ((pattern, 'secret-1'), (cost, '99'), (start, '2030')):
            with self.subTest(rule=rule.rule_type):
                outcome = self._outcomes(rule, product)[(product.id, rule.id)]
                self.assertEqual(outcome.status, FAIL)
                self.assertNotIn('value', outcome.params)
                self.assertTrue(outcome.params['value_hidden'])
                sentence = self._explain(outcome)
                self.assertTrue(sentence)
                self.assertNotIn(secret, sentence)

    def test_explanation_of_a_removed_field_falls_back_to_its_name(self):
        """A stored explanation still renders once its field or model is gone."""
        sentence = validation.explain(self.env, 'range_below', {
            'model': 'product.template', 'field': 'x_dgq_removed', 'value': -5, 'min': 0})
        self.assertIn('x_dgq_removed', sentence)
        sentence = validation.explain(self.env, 'required_empty', {
            'model': 'x_dgq.gone', 'fields': ['x_name']})
        self.assertIn('x_name', sentence)
