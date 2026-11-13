import random

from odoo import Command
from odoo.exceptions import ValidationError
from odoo.tests import Form

from .acceptance_m2b_helpers import M2BCase


class TestDuplicateRuleAcceptance(M2BCase):
    """Acceptance tests of T-016: duplicate rules, scoring and group detection.

    The duplicate rule screen is driven with ``Form`` (name, model, the matching-field lines,
    threshold); a duplicate check is a background job started on ``data.quality.duplicate.check``
    and run by the real runner. Groups are read through ``rule.group_ids`` and their lines.

    Requirements: BR-DGQ-0012, FR-DGQ-0038, FR-DGQ-0039
    """

    # ---- FR-DGQ-0038 -------------------------------------------------------

    def test_manager_saves_a_rule_and_the_next_check_uses_it(self):
        """SC-DGQ-0038-1: the Contact rule (name fuzzy 2, e-mail normalized 3, threshold 85) is stored and used."""
        Rule = self.env['data.quality.duplicate.rule'].with_user(self.manager_a)
        with Form(Rule) as form:
            form.name = 'Contacts by name and e-mail'
            form.model_id = self.model('res.partner')
            form.company_id = self.cx
            form.threshold = 85
            with form.field_line_ids.new() as line:
                line.field_id = self.fld('res.partner', 'name')
                line.method = 'fuzzy'
                line.weight = 2
            with form.field_line_ids.new() as line:
                line.field_id = self.fld('res.partner', 'email')
                line.method = 'normalized'
                line.weight = 3
        rule = form.record.sudo()
        self.assertEqual(rule.threshold, 85)
        self.assertEqual(sorted(rule.field_line_ids.mapped('method')), ['fuzzy', 'normalized'])
        self.assertEqual(sorted(rule.field_line_ids.mapped('weight')), [2, 3])
        one = self.partner('Acme Ltd', email='info@acme.com')
        two = self.partner('ACME Ltd.', email='INFO@acme.com')
        self.run_check(rule)
        self.assertTrue(self.group_of(rule, one))
        self.assertEqual(self.group_of(rule, one), self.group_of(rule, two))

    def test_default_threshold_is_85(self):
        """FR-DGQ-0038: a rule saved without a threshold gets 85."""
        rule = self.env['data.quality.duplicate.rule'].create({
            'name': 'Default threshold',
            'model_id': self.model('res.partner').id,
            'field_line_ids': [Command.create({
                'field_id': self.fld('res.partner', 'email').id, 'method': 'exact',
                'weight': 1})],
        })
        self.assertEqual(rule.threshold, 85)

    def test_fuzzy_matching_on_a_number_field_is_refused(self):
        """SC-DGQ-0038-2: fuzzy matching on a number field is refused: it applies to text fields only."""
        with self.assertRaises(ValidationError) as caught:
            self.dup_rule('res.partner', [('color', 'fuzzy', 2)])
        self.assertIn('text', str(caught.exception).lower())

    def test_fuzzy_matching_on_a_text_field_is_accepted(self):
        """FR-DGQ-0038: fuzzy matching on a text field is saved."""
        rule = self.dup_rule('res.partner', [('name', 'fuzzy', 2)])
        self.assertEqual(rule.field_line_ids.method, 'fuzzy')

    def test_weight_boundaries(self):
        """FR-DGQ-0038: weights 1 and 10 are saved; 0 and 11 are refused."""
        for weight in (1, 10):
            rule = self.dup_rule('res.partner', [('name', 'exact', weight)])
            self.assertEqual(rule.field_line_ids.weight, weight)
        for weight in (0, 11):
            with self.assertRaises(ValidationError, msg='weight %s' % weight):
                self.dup_rule('res.partner', [('name', 'exact', weight)])

    def test_threshold_boundaries(self):
        """FR-DGQ-0038: thresholds 50 and 100 are saved; 49 and 101 are refused."""
        for threshold in (50, 100):
            rule = self.dup_rule('res.partner', [('name', 'exact', 1)], threshold=threshold)
            self.assertEqual(rule.threshold, threshold)
        for threshold in (49, 101):
            with self.assertRaises(ValidationError, msg='threshold %s' % threshold):
                self.dup_rule('res.partner', [('name', 'exact', 1)], threshold=threshold)

    def test_a_rule_needs_at_least_one_matching_field(self):
        """FR-DGQ-0038: a rule without any matching field is refused."""
        with self.assertRaises(ValidationError):
            self.env['data.quality.duplicate.rule'].create({
                'name': 'No field', 'model_id': self.model('res.partner').id, 'threshold': 85})

    # ---- FR-DGQ-0039 -------------------------------------------------------

    def test_similar_contacts_form_one_group(self):
        """SC-DGQ-0039-1: 'Acme Ltd' and 'ACME Ltd.' form one group scoring at least 85; 'Beta' is in none."""
        rule = self.contact_rule()
        one = self.partner('Acme Ltd', email='info@acme.com')
        two = self.partner('ACME Ltd.', email='INFO@acme.com')
        beta = self.partner('Beta', email='beta@b.com')
        self.run_check(rule)
        groups = self.open_groups(rule)
        self.assertEqual(len(groups), 1)
        self.assertEqual(self.group_ids(groups), {one.id, two.id})
        self.assertGreaterEqual(groups.score, 85)
        self.assertFalse(self.group_of(rule, beta))
        scores = groups.line_ids.sorted('sequence').mapped('score')
        self.assertGreaterEqual(min(scores), 85)

    def test_contacts_of_two_companies_are_never_grouped(self):
        """SC-DGQ-0039-2: two identical contacts 'Acme' of company A and B are not in the same group."""
        rule = self.dup_rule('res.partner', [('name', 'exact', 2), ('email', 'normalized', 3)],
                             company=False)
        in_x = self.partner('Acme', self.cx, email='acme@acme.com')
        in_y = self.partner('Acme', self.cy, email='acme@acme.com')
        self.run_check(rule)
        self.assertFalse(self.group_of(rule, in_x) & self.group_of(rule, in_y))

    def test_a_repeated_check_keeps_the_same_groups(self):
        """SC-DGQ-0039-3: a check with 2 open groups run again with no data change still gives exactly 2."""
        rule = self.contact_rule()
        self.partner('Acme Ltd', email='info@acme.com')
        self.partner('ACME Ltd.', email='INFO@acme.com')
        self.partner('Gamma Trading', email='sales@gamma.com')
        self.partner('GAMMA Trading', email='SALES@gamma.com')
        self.run_check(rule)
        first = self.open_groups(rule)
        self.assertEqual(len(first), 2)
        self.run_check(rule)
        self.assertEqual(len(self.open_groups(rule)), 2)

    def test_records_of_other_filters_and_archived_records_are_left_out(self):
        """FR-DGQ-0039: only active records that pass the rule's filter are scored."""
        rule = self.contact_rule(domain="[('city', '=', 'Lyon')]")
        lyon = self.partner('Delta SA', email='d@delta.com', city='Lyon')
        lyon_two = self.partner('DELTA SA', email='D@delta.com', city='Lyon')
        paris = self.partner('Delta SA', email='d@delta.com', city='Paris')
        gone = self.partner('Delta SA', email='d@delta.com', city='Lyon')
        gone.active = False
        self.run_check(rule)
        group = self.group_of(rule, lyon)
        self.assertEqual(self.group_ids(group), {lyon.id, lyon_two.id})
        self.assertFalse(self.group_of(rule, paris))

    # ---- BR-DGQ-0012 -------------------------------------------------------

    def test_pair_score_is_the_weighted_average_of_the_field_scores(self):
        """BR-DGQ-0012: name exact (weight 2) 0 and e-mail normalized (weight 3) 100 give (0*2+100*3)/5 = 60."""
        rule = self.dup_rule('res.partner', [('name', 'exact', 2), ('email', 'normalized', 3)],
                             threshold=50)
        one = self.partner('Orion', email='o@orion.com')
        two = self.partner('Orioz', email='O@ORION.COM')
        self.run_check(rule)
        group = self.group_of(rule, one)
        self.assertEqual(self.group_ids(group), {one.id, two.id})
        self.assertAlmostEqual(group.score, 60.0, places=1)

    def test_a_pair_at_the_threshold_is_shown_and_one_just_below_is_not(self):
        """BR-DGQ-0012 / FR-DGQ-0039: a pair scoring 60 appears at threshold 60 and not at 61."""
        at = self.dup_rule('res.partner', [('name', 'exact', 2), ('email', 'normalized', 3)],
                           threshold=60)
        above = self.dup_rule('res.partner', [('name', 'exact', 2), ('email', 'normalized', 3)],
                              threshold=61)
        one = self.partner('Orion', email='o@orion.com')
        self.partner('Orioz', email='O@ORION.COM')
        self.run_check(at)
        self.run_check(above)
        self.assertTrue(self.group_of(at, one))
        self.assertFalse(self.group_of(above, one))

    def test_exact_matching_is_as_stored_and_normalized_ignores_case_accents_and_spaces(self):
        """BR-DGQ-0012: exact is 100 or 0 as stored; normalized ignores case, accents, spaces and punctuation."""
        exact = self.dup_rule('res.partner', [('name', 'exact', 1)], threshold=100)
        normalized = self.dup_rule('res.partner', [('name', 'normalized', 1)], threshold=100)
        one = self.partner('Café  Noir!', email='x1@x.com')
        two = self.partner('cafe noir', email='x2@x.com')
        self.run_check(exact)
        self.run_check(normalized)
        self.assertFalse(self.group_of(exact, one))
        self.assertEqual(self.group_ids(self.group_of(normalized, one)), {one.id, two.id})

    def test_normalized_phones_compare_as_digits_only(self):
        """BR-DGQ-0012: phone numbers are equal after keeping only the digits."""
        rule = self.dup_rule('res.partner', [('phone', 'normalized', 1)], threshold=100)
        one = self.partner('Phone One', phone='+1 (555) 010-0100')
        two = self.partner('Phone Two', phone='1555 010 0100')
        self.run_check(rule)
        self.assertEqual(self.group_ids(self.group_of(rule, one)), {one.id, two.id})

    def test_an_empty_value_leaves_the_field_out_of_the_average(self):
        """BR-DGQ-0012: the e-mail empty on one side leaves it out: equal names alone score 100."""
        rule = self.dup_rule('res.partner', [('name', 'exact', 2), ('email', 'normalized', 3)],
                             threshold=100)
        one = self.partner('Same Name', email=False)
        two = self.partner('Same Name', email='some@one.com')
        self.run_check(rule)
        group = self.group_of(rule, one)
        self.assertEqual(self.group_ids(group), {one.id, two.id})
        self.assertAlmostEqual(group.score, 100.0, places=1)

    def test_a_group_links_every_record_joined_by_a_pair_above_the_threshold(self):
        """FR-DGQ-0039: three records linked in a chain by pairs at or above the threshold are one group."""
        rule = self.dup_rule('res.partner', [('email', 'normalized', 1)], threshold=100)
        one = self.partner('Chain One', email='chain@c.com')
        two = self.partner('Chain Two', email=' CHAIN@c.com ')
        three = self.partner('Chain Three', email='Chain@C.com')
        self.run_check(rule)
        self.assertEqual(self.group_ids(self.group_of(rule, one)), {one.id, two.id, three.id})

    # ---- NFR-DGQ-0011 ------------------------------------------------------

    def test_reference_set_of_1000_contacts_finds_90_percent_with_5_percent_false(self):
        """NFR-DGQ-0011: 1,000 contacts with 100 known pairs: at least 90 found, at most 5 percent of shown pairs false.

        The set is generated here from a fixed seed (the delivered reference set is not a file of
        the spec); the rule is the Contact rule of the scenarios at its default threshold.
        """
        rnd = random.Random(20261113)
        used = set()

        def word():
            while True:
                text = ''.join(rnd.choice('bcdfghjklmnprstvz') + rnd.choice('aeiou')
                               for _ in range(rnd.randint(3, 4)))
                if text not in used:
                    used.add(text)
                    return text.capitalize()

        values, pairs = [], []
        for index in range(100):
            name = '%s %s' % (word(), word())
            email = 'p%03d.%s@%s.com' % (index, word().lower(), word().lower())
            cut = rnd.randint(2, len(name) - 3)
            values.append({'name': name, 'email': email, 'company_id': self.cx.id})
            values.append({'name': name[:cut] + name[cut + 1:], 'email': email.upper(),
                           'company_id': self.cx.id})
            pairs.append((2 * index, 2 * index + 1))
        for index in range(800):
            values.append({'name': '%s %s' % (word(), word()),
                           'email': 'u%03d.%s@%s.com' % (index, word().lower(), word().lower()),
                           'company_id': self.cx.id})
        partners = self.env['res.partner'].create(values)
        self.assertEqual(len(partners), 1000)
        rule = self.contact_rule()
        self.run_check(rule)
        known = {frozenset((partners[a].id, partners[b].id)) for a, b in pairs}
        shown = set()
        for group in self.open_groups(rule):
            ids = sorted(self.group_ids(group))
            for position, first in enumerate(ids):
                for second in ids[position + 1:]:
                    shown.add(frozenset((first, second)))
        found = len(known & shown)
        false = len(shown - known)
        self.assertGreaterEqual(found, 90)
        self.assertLessEqual(false, 0.05 * len(shown))
