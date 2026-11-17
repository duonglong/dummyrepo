from unittest.mock import patch

from odoo.exceptions import UserError

from .acceptance_helpers import AcceptanceCase, choice

CODE_FIELD_NAMES = {
    'python_code', 'code', 'expression', 'formula', 'eval_code', 'validator_code', 'script', 'python',
}


class TestValidationAdvancedAcceptance(AcceptanceCase):
    """Acceptance tests of T-007: link, condition, cross-field checks and custom validators.

    Every test runs a real scan and reads the result lines and counts; the rule form step is left
    to the build (the rule is created through the registry).

    Requirements: BR-DGQ-0009, FR-DGQ-0006, FR-DGQ-0007, FR-DGQ-0008, FR-DGQ-0009
    """

    def _counts(self, scan):
        return (scan.checks_passed, scan.checks_failed, scan.checks_not_applicable)

    def _register(self, *descriptors):
        """Register validators the documented way: a module extends the registry with super()."""
        original = type(self.env['data.quality.registry'])

        def extended(this):
            return super(original, this)._dgq_validators() + list(descriptors)

        patcher = patch.object(original, '_dgq_validators', extended, create=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    # ---- FR-DGQ-0006 link integrity ---------------------------------------

    def test_link_to_an_archived_record_fails(self):
        """SC-DGQ-0006-1: 'Acme' whose salesperson is archived fails; an active salesperson passes."""
        rule = self.make_rule('res.partner', 'link', ['user_id'])
        salesperson = self.user('operator', login='dgq_m2_sales')
        gone = self.user('operator', login='dgq_m2_gone')
        acme = self.partner('Acme', user_id=gone.id)
        beta = self.partner('Beta', user_id=salesperson.id)
        empty = self.partner('Empty')
        gone.active = False
        scan = self.run_scan([rule], models=['res.partner'])
        failed = self.failing(scan, rule)
        self.assertIn(acme.id, failed)
        self.assertIn('archived', self.text(failed[acme.id]).lower())
        self.assertNotIn(beta.id, failed)
        self.assertNotIn(empty.id, failed)  # an empty link is left to the required field rules

    def test_link_to_another_company_fails_unless_shared(self):
        """SC-DGQ-0006-2: a pricelist of another company fails; one shared by all companies passes."""
        rule = self.make_rule('res.partner', 'link', ['x_dgq_list'])
        Pricelist = self.env['product.pricelist']
        own = Pricelist.create({'name': 'Own', 'company_id': self.cx.id})
        other = Pricelist.create({'name': 'Other', 'company_id': self.cy.id})
        shared = Pricelist.create({'name': 'Shared', 'company_id': False})
        crossed = self.partner('Crossed', x_dgq_list=other.id)
        fine = self.partner('Fine', x_dgq_list=own.id)
        open_ = self.partner('Open', x_dgq_list=shared.id)
        scan = self.run_scan([rule], models=['res.partner'])
        failed = self.failing(scan, rule)
        self.assertIn(crossed.id, failed)
        self.assertIn('company', self.text(failed[crossed.id]).lower())
        self.assertNotIn(fine.id, failed)
        self.assertNotIn(open_.id, failed)

    def test_link_to_an_archived_pricelist_fails(self):
        """FR-DGQ-0006: a link to an archived record fails whatever the kind of record."""
        rule = self.make_rule('res.partner', 'link', ['x_dgq_list'])
        pricelist = self.env['product.pricelist'].create({'name': 'Old', 'company_id': self.cx.id})
        acme = self.partner('Acme', x_dgq_list=pricelist.id)
        pricelist.active = False
        failed = self.failing(self.run_scan([rule], models=['res.partner']), rule)
        self.assertIn(acme.id, failed)

    # ---- FR-DGQ-0007 condition --------------------------------------------

    def test_condition_applies_and_must_satisfy(self):
        """SC-DGQ-0007-1: a company missing the required value fails, one that has it passes, a person is not applicable.

        Odoo 20 computes 'Is a company' from the tax ID (a partner without a tax ID is a person), so a
        company 'without tax ID' cannot exist. The scenario's shape is kept with 'Website is set' as the
        policy: Acme (company, no website) fails, Beta (company, website) passes, John (person) is not
        applicable.
        """
        rule = self.make_rule(
            'res.partner', 'condition', applies_domain="[('is_company', '=', True)]",
            condition_domain="[('website', '!=', False)]")
        before = self._counts(self.run_scan([rule], models=['res.partner']))
        acme = self.partner('Acme', vat='BE0403019261')
        beta = self.partner('Beta', vat='BE0477472701', website='https://beta.example.com')
        john = self.partner('John')
        self.assertTrue(acme.is_company and beta.is_company and not john.is_company)
        scan = self.run_scan([rule], models=['res.partner'])
        failed = self.failing(scan, rule)
        self.assertEqual(set(failed) & {acme.id, beta.id, john.id}, {acme.id})
        after = self._counts(scan)
        self.assertEqual(tuple(a - b for a, b in zip(after, before)), (1, 1, 1))

    def test_condition_with_an_unknown_field_is_refused(self):
        """SC-DGQ-0007-2: a filter on a field the model does not have is refused, naming the field."""
        for name in ('applies_domain', 'condition_domain'):
            vals = {'applies_domain': "[('is_company', '=', True)]",
                    'condition_domain': "[('vat', '!=', False)]"}
            vals[name] = "[('x_no_such_field', '=', 1)]"
            with self.assertRaises(UserError) as caught:
                self.make_rule('res.partner', 'condition', **vals)
            self.assertIn('x_no_such_field', str(caught.exception))

    def test_condition_domain_is_never_evaluated_as_code(self):
        """BR-DGQ-0009: a filter that holds code instead of a domain is refused, never run."""
        with self.assertRaises(UserError):
            self.make_rule(
                'res.partner', 'condition', applies_domain="[('is_company', '=', True)]",
                condition_domain="[('name', '=', __import__('os').getcwd())]")

    # ---- FR-DGQ-0008 cross-field ------------------------------------------

    def _cross_rule(self, field, other, include, exclude=(), keys=(), model='product.template'):
        operator = choice(self.env, 'data.quality.rule', 'compare_operator', include, exclude, keys)
        return self.make_rule(
            model, 'cross_field', [field], compare_field_id=self.fld(model, other).id,
            compare_operator=operator)

    def test_cross_field_price_not_below_cost(self):
        """SC-DGQ-0008-1: 'Pen' sells at 2 for a cost of 3 and fails, quoting both values."""
        rule = self._cross_rule('list_price', 'x_dgq_cost', ['greater', 'equal'], ['lower', 'not'], ('>=',))
        pen = self.product('Pen', list_price=2, x_dgq_cost=3)
        equal = self.product('Equal', list_price=3, x_dgq_cost=3)
        above = self.product('Above', list_price=4, x_dgq_cost=3)
        failed = self.failing(self.run_scan([rule], models=['product.template']), rule)
        self.assertIn(pen.id, failed)
        explanation = self.text(failed[pen.id])
        self.assertIn('2', explanation)
        self.assertIn('3', explanation)
        self.assertNotIn(equal.id, failed)
        self.assertNotIn(above.id, failed)

    def test_cross_field_strict_greater_fails_on_equal(self):
        """FR-DGQ-0008: with 'greater', two equal values fail."""
        rule = self._cross_rule('list_price', 'x_dgq_cost', ['greater'], ['equal', 'lower', 'not'], ('>',))
        equal = self.product('Equal', list_price=3, x_dgq_cost=3)
        above = self.product('Above', list_price=4, x_dgq_cost=3)
        failed = self.failing(self.run_scan([rule], models=['product.template']), rule)
        self.assertIn(equal.id, failed)
        self.assertNotIn(above.id, failed)

    def test_cross_field_end_date_not_before_start(self):
        """FR-DGQ-0008: an end before the start fails; an empty field makes the record not applicable."""
        rule = self._cross_rule('x_dgq_end', 'x_dgq_start', ['greater', 'equal'], ['lower', 'not'], ('>=',))
        before = self._counts(self.run_scan([rule], models=['product.template']))
        backwards = self.product('Backwards', x_dgq_start='2026-01-10', x_dgq_end='2026-01-05')
        forwards = self.product('Forwards', x_dgq_start='2026-01-10', x_dgq_end='2026-01-20')
        open_ended = self.product('Open', x_dgq_start='2026-01-10')
        scan = self.run_scan([rule], models=['product.template'])
        failed = self.failing(scan, rule)
        self.assertEqual(set(failed) & {backwards.id, forwards.id, open_ended.id}, {backwards.id})
        after = self._counts(scan)
        self.assertEqual(tuple(a - b for a, b in zip(after, before)), (1, 1, 1))

    def test_cross_field_of_unlike_kinds_is_refused(self):
        """SC-DGQ-0008-2: a date compared with a text is refused with a message that they cannot be compared."""
        operator = choice(self.env, 'data.quality.rule', 'compare_operator', ['equal'], ['not'], ('=',))
        with self.assertRaises(UserError) as caught:
            self.make_rule(
                'product.template', 'cross_field', ['x_dgq_start'],
                compare_field_id=self.fld('product.template', 'name').id, compare_operator=operator)
        self.assertIn('compar', str(caught.exception).lower())

    # ---- FR-DGQ-0009 / BR-DGQ-0009 custom validators ------------------------

    def test_registered_validator_is_offered_and_no_code_field_exists(self):
        """SC-DGQ-0009-1: the validator an installed module registers is offered; no field takes code."""
        self._register({'key': 'iban_checksum', 'label': 'IBAN checksum', 'handler': lambda env, rule, records: {}})
        Rule = self.env['data.quality.rule']
        offered = dict(Rule._fields['validator_key']._description_selection(self.env))
        self.assertEqual(offered.get('iban_checksum'), 'IBAN checksum')
        self.assertFalse(CODE_FIELD_NAMES & set(Rule._fields))
        rule = self.make_rule('res.partner', 'custom', validator_key='iban_checksum')
        self.assertEqual(rule.validator_key, 'iban_checksum')

    def test_registered_validator_failure_is_reported(self):
        """FR-DGQ-0009: the validator's reason becomes the explanation of the failed record."""
        def handler(env, rule, records):
            return {record.id: 'IBAN checksum is wrong' for record in records if record.name.startswith('Bad')}

        self._register({'key': 'iban_checksum', 'label': 'IBAN checksum', 'handler': handler})
        rule = self.make_rule('res.partner', 'custom', validator_key='iban_checksum')
        bad = self.partner('Bad one')
        good = self.partner('Good one')
        failed = self.failing(self.run_scan([rule], models=['res.partner']), rule)
        self.assertIn(bad.id, failed)
        self.assertIn('IBAN checksum is wrong', self.text(failed[bad.id]))
        self.assertNotIn(good.id, failed)

    def test_validator_error_fails_only_its_record(self):
        """SC-DGQ-0009-2: an error on 1 record of 100 is reported on it; the 99 others are checked."""
        def handler(env, rule, records):
            if any(record.name == 'Broken' for record in records):
                raise ValueError('validator exploded')
            return {}

        self._register({'key': 'fragile', 'label': 'Fragile', 'handler': handler})
        rule = self.make_rule('res.partner', 'custom', validator_key='fragile')
        broken = self.partner('Broken')
        for number in range(99):
            self.partner('Fine %s' % number)
        total = self.env['res.partner'].search_count([('company_id', '=', self.cx.id)])
        scan = self.run_scan([rule], models=['res.partner'])
        self.assertEqual(scan.state, 'done')
        failed = self.failing(scan, rule)
        self.assertEqual(list(failed), [broken.id])
        self.assertIn('validator exploded', self.text(failed[broken.id]))
        self.assertEqual(scan.records_scanned, total)

    def test_unregistered_validator_never_runs(self):
        """BR-DGQ-0009: a key that no installed module registers is refused or never run."""
        victim = self.partner('Victim')
        try:
            rule = self.make_rule('res.partner', 'custom', validator_key='os.system')
        except UserError:
            return
        scan = self.run_scan([rule], models=['res.partner'])
        self.assertEqual(scan.state, 'done')
        self.assertNotIn(victim.id, self.failing(scan, rule))
