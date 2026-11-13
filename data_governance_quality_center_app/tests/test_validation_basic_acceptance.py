from odoo.exceptions import UserError

from .acceptance_helpers import AcceptanceCase, choice

PATTERN = r'[A-Z]{3}-[0-9]{4}'


class TestValidationBasicAcceptance(AcceptanceCase):
    """Acceptance tests of T-006: required field, pattern, number range and date checks.

    Every test runs a real scan (create, start, background runner) and reads the result lines.
    The scan form step is left to the build; the scan is created through the registry.

    Requirements: FR-DGQ-0002, FR-DGQ-0003, FR-DGQ-0004, FR-DGQ-0005
    """

    def _scan_products(self, rule):
        return self.failing(self.run_scan([rule], models=['product.template']), rule)

    def _scan_partners(self, rule):
        return self.failing(self.run_scan([rule], models=['res.partner']), rule)

    # ---- FR-DGQ-0002 required field ---------------------------------------

    def test_required_fails_when_any_field_is_empty(self):
        """SC-DGQ-0002-1: 'Desk' has a reference and no barcode: it fails, explained by the barcode."""
        rule = self.make_rule('product.template', 'required', ['default_code', 'x_dgq_barcode'])
        desk = self.product('Desk', default_code='D-1')
        full = self.product('Chair', default_code='C-1', x_dgq_barcode='123')
        no_ref = self.product('Stool', x_dgq_barcode='456')
        failed = self._scan_products(rule)
        self.assertIn(desk.id, failed)
        explanation = self.text(failed[desk.id])
        self.assertTrue('DGQ Barcode' in explanation or 'x_dgq_barcode' in explanation, explanation)
        self.assertNotIn(full.id, failed)
        self.assertIn(no_ref.id, failed)

    def test_required_text_of_spaces_is_empty(self):
        """SC-DGQ-0002-2: an e-mail of three spaces fails; a real e-mail and a missing one are read right."""
        rule = self.make_rule('res.partner', 'required', ['email'])
        acme = self.partner('Acme', email='   ')
        blank = self.partner('Blank')
        beta = self.partner('Beta', email='beta@example.com')
        failed = self._scan_partners(rule)
        self.assertIn(acme.id, failed)
        self.assertIn(blank.id, failed)
        self.assertNotIn(beta.id, failed)

    def test_required_number_zero_is_a_value(self):
        """SC-DGQ-0002-3: a sales price of 0 is a real value and passes."""
        rule = self.make_rule('product.template', 'required', ['list_price'])
        sample = self.product('Sample', list_price=0)
        failed = self._scan_products(rule)
        self.assertNotIn(sample.id, failed)

    def test_required_list_of_links_empty_fails(self):
        """FR-DGQ-0002: a zero-length list of links is empty, a list with one link is filled."""
        rule = self.make_rule('res.partner', 'required', ['category_id'])
        tag = self.env['res.partner.category'].create({'name': 'DGQ tag'})
        bare = self.partner('Bare')
        tagged = self.partner('Tagged', category_id=[(6, 0, [tag.id])])
        failed = self._scan_partners(rule)
        self.assertIn(bare.id, failed)
        self.assertNotIn(tagged.id, failed)

    # ---- FR-DGQ-0003 pattern ----------------------------------------------

    def test_pattern_full_match_only(self):
        """SC-DGQ-0003-1: 'ABC-1234' passes; 'abc-12' fails quoting its value; partial matches fail too."""
        rule = self.make_rule('product.template', 'pattern', ['default_code'], pattern=PATTERN)
        good = self.product('Good', default_code='ABC-1234')
        bad = self.product('Bad', default_code='abc-12')
        longer = self.product('Longer', default_code='ABC-12345')
        padded = self.product('Padded', default_code=' ABC-1234')
        failed = self._scan_products(rule)
        self.assertNotIn(good.id, failed)
        self.assertIn(bad.id, failed)
        self.assertIn('abc-12', self.text(failed[bad.id]))
        self.assertIn(longer.id, failed)
        self.assertIn(padded.id, failed)

    def test_pattern_leaves_empty_to_required_rules(self):
        """SC-DGQ-0003-2: a product with no reference is not failed by the pattern rule."""
        rule = self.make_rule('product.template', 'pattern', ['default_code'], pattern=PATTERN)
        empty = self.product('Empty')
        failed = self._scan_products(rule)
        self.assertNotIn(empty.id, failed)

    def test_pattern_with_unclosed_bracket_is_refused(self):
        """SC-DGQ-0003-3: a pattern that does not compile is refused with a message about the pattern."""
        with self.assertRaises(UserError) as caught:
            self.make_rule('product.template', 'pattern', ['default_code'], pattern='[A-Z')
        self.assertIn('pattern', str(caught.exception).lower())

    # ---- FR-DGQ-0004 number range -----------------------------------------

    def _range_rule(self, **vals):
        return self.make_rule('product.template', 'range', ['x_dgq_cost'], **vals)

    def test_range_bounds_are_inside(self):
        """SC-DGQ-0004-1: costs -5 and 12000 fail; 0 and 10000 pass; just outside either bound fails."""
        rule = self._range_rule(has_min=True, min_value=0, has_max=True, max_value=10000)
        costs = {-5: True, -0.01: True, 0: False, 10000: False, 10000.01: True, 12000: True}
        products = {cost: self.product('Cost %s' % cost, x_dgq_cost=cost) for cost in costs}
        failed = self._scan_products(rule)
        for cost, fails in costs.items():
            self.assertEqual(products[cost].id in failed, fails, cost)
        explanation = self.text(failed[products[-5].id]).lower()
        self.assertIn('-5', explanation)
        self.assertIn('minimum', explanation)

    def test_range_with_only_a_minimum_checks_a_floor(self):
        """FR-DGQ-0004: a rule with only a minimum has no ceiling."""
        rule = self._range_rule(has_min=True, min_value=0, has_max=False)
        low = self.product('Low', x_dgq_cost=-1)
        high = self.product('High', x_dgq_cost=10 ** 9)
        failed = self._scan_products(rule)
        self.assertIn(low.id, failed)
        self.assertNotIn(high.id, failed)

    def test_range_with_only_a_maximum_checks_a_ceiling(self):
        """FR-DGQ-0004: a rule with only a maximum has no floor."""
        rule = self._range_rule(has_min=False, has_max=True, max_value=100)
        low = self.product('Low', x_dgq_cost=-10 ** 6)
        high = self.product('High', x_dgq_cost=101)
        failed = self._scan_products(rule)
        self.assertNotIn(low.id, failed)
        self.assertIn(high.id, failed)

    def test_range_minimum_above_maximum_is_refused(self):
        """SC-DGQ-0004-2: minimum 100 and maximum 10 are refused with a message naming both."""
        with self.assertRaises(UserError) as caught:
            self._range_rule(has_min=True, min_value=100, has_max=True, max_value=10)
        message = str(caught.exception).lower()
        self.assertIn('minimum', message)
        self.assertIn('maximum', message)

    def test_range_without_a_bound_is_refused(self):
        """FR-DGQ-0004: a range rule with neither bound cannot work and is refused."""
        with self.assertRaises(UserError):
            self._range_rule(has_min=False, has_max=False)

    # ---- FR-DGQ-0005 date --------------------------------------------------

    def _date_rule(self, include, exclude=(), field='x_dgq_born_on', model='res.partner', **vals):
        condition = choice(self.env, 'data.quality.rule', 'date_condition', include, exclude)
        return self.make_rule(model, 'date', [field], date_condition=condition, **vals)

    def test_date_not_in_the_future(self):
        """SC-DGQ-0005-1: a birth date of today passes, a date ahead fails; no date is not failed."""
        rule = self._date_rule(['future'], ['days', 'ahead', 'more'])
        today = self.partner('Today', x_dgq_born_on=self.day())
        tomorrow = self.partner('Tomorrow', x_dgq_born_on=self.day(1))
        later = self.partner('Later', x_dgq_born_on=self.day(200))
        none = self.partner('None')
        failed = self._scan_partners(rule)
        self.assertNotIn(today.id, failed)
        self.assertIn(tomorrow.id, failed)
        self.assertIn(later.id, failed)
        self.assertNotIn(none.id, failed)

    def test_date_not_in_the_past(self):
        """FR-DGQ-0005: today passes 'not in the past', yesterday fails."""
        rule = self._date_rule(['past'], ['days', 'older'])
        today = self.partner('Today', x_dgq_born_on=self.day())
        yesterday = self.partner('Yesterday', x_dgq_born_on=self.day(-1))
        failed = self._scan_partners(rule)
        self.assertNotIn(today.id, failed)
        self.assertIn(yesterday.id, failed)

    def test_date_not_older_than_days(self):
        """SC-DGQ-0005-2: a product last updated 377 days ago fails the 365-day rule, with its date and limit."""
        rule = self._date_rule(['older'], field='write_date', model='product.template', date_days=365)
        old = self.product('Old')
        edge = self.product('Edge')
        over = self.product('Over')
        fresh = self.product('Fresh')
        moment = self.set_write_date(old, 377)
        self.set_write_date(edge, 365)
        self.set_write_date(over, 366)
        failed = self._scan_products(rule)
        self.assertIn(old.id, failed)
        explanation = self.text(failed[old.id])
        self.assertIn(str(moment.date()), explanation)
        self.assertIn('365', explanation)
        self.assertNotIn(edge.id, failed)  # exactly 365 days old is not older than 365 days
        self.assertIn(over.id, failed)
        self.assertNotIn(fresh.id, failed)

    def test_date_not_more_than_days_ahead(self):
        """FR-DGQ-0005: 30 days ahead passes a 30-day limit, 31 fails."""
        rule = self._date_rule(['ahead'], date_days=30)
        edge = self.partner('Edge', x_dgq_born_on=self.day(30))
        over = self.partner('Over', x_dgq_born_on=self.day(31))
        failed = self._scan_partners(rule)
        self.assertNotIn(edge.id, failed)
        self.assertIn(over.id, failed)

    def test_date_between_two_fixed_dates(self):
        """FR-DGQ-0005: both fixed dates count as inside; a day before or after fails."""
        rule = self._date_rule(['between'], date_from='2020-01-01', date_to='2020-12-31')
        inside = {
            'first': ('2020-01-01', False), 'last': ('2020-12-31', False),
            'before': ('2019-12-31', True), 'after': ('2021-01-01', True),
        }
        partners = {key: self.partner(key, x_dgq_born_on=value[0]) for key, value in inside.items()}
        failed = self._scan_partners(rule)
        for key, (_value, fails) in inside.items():
            self.assertEqual(partners[key].id in failed, fails, key)
