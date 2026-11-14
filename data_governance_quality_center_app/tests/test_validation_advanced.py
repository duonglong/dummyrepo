from unittest.mock import patch

from odoo.addons.data_governance_quality_center_app.services import validation
from odoo.addons.data_governance_quality_center_app.services.validation import FAIL, NOT_APPLICABLE, PASS

from .acceptance_helpers import AcceptanceCase


class TestValidationAdvanced(AcceptanceCase):
    """The advanced checks of the validation service, called without a scan.

    Requirements: FR-DGQ-0006, FR-DGQ-0007, FR-DGQ-0008, FR-DGQ-0009, BR-DGQ-0009
    """

    def _outcomes(self, rule, *records):
        records = records[0].browse([r.id for r in records])
        return {rid: outcome for (rid, _rule), outcome in validation.check(rule, records).items()}

    def _register(self, key, handler, **extra):
        registry_class = type(self.env['data.quality.registry'])

        def extended(this):
            return super(registry_class, this)._dgq_validators() + [
                dict(key=key, label=key, handler=handler, **extra)]

        patcher = patch.object(registry_class, '_dgq_validators', extended, create=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _explain(self, outcome):
        return validation.explain(self.env, outcome.code, outcome.params)

    def test_link_codes_and_explanations(self):
        """FR-DGQ-0006: archived and crossed-company links fail with their own code; shared and empty do not."""
        rule = self.make_rule('res.partner', 'link', ['x_dgq_list'], company=False)
        Pricelist = self.env['product.pricelist']
        old = Pricelist.create({'name': 'Old', 'company_id': self.cx.id})
        other = Pricelist.create({'name': 'Other', 'company_id': self.cy.id})
        shared = Pricelist.create({'name': 'Shared', 'company_id': False})
        archived = self.partner('A', x_dgq_list=old.id)
        crossed = self.partner('B', x_dgq_list=other.id)
        fine = self.partner('C', x_dgq_list=shared.id)
        empty = self.partner('D')
        old.active = False
        out = self._outcomes(rule, archived, crossed, fine, empty)
        self.assertEqual(out[archived.id].code, 'link_archived')
        self.assertIn('archived', self._explain(out[archived.id]))
        self.assertEqual(out[crossed.id].code, 'link_other_company')
        self.assertIn('another company', self._explain(out[crossed.id]))
        self.assertEqual(out[fine.id].status, PASS)
        self.assertEqual(out[empty.id].status, NOT_APPLICABLE)

    def test_link_to_a_deleted_record_fails(self):
        """FR-DGQ-0006: a link whose target row is gone fails."""
        rule = self.make_rule('res.partner', 'link', ['user_id'], company=False)
        user = self.user('operator', login='dgq_m2b_gone')
        acme = self.partner('Acme', user_id=user.id)
        self.env.cr.execute('ALTER TABLE res_partner DROP CONSTRAINT IF EXISTS res_partner_user_id_fkey')
        self.env.cr.execute('UPDATE res_partner SET user_id = %s WHERE id = %s', (user.id + 100000, acme.id))
        acme.invalidate_recordset()
        self.assertEqual(self._outcomes(rule, acme)[acme.id].code, 'link_missing')

    def test_condition_three_ways(self):
        """FR-DGQ-0007: applies and fails, applies and passes, outside the scope; no filter applies to all."""
        rule = self.make_rule(
            'res.partner', 'condition', company=False, applies_domain="[('is_company', '=', True)]",
            condition_domain="[('vat', '!=', False)]")
        acme = self.partner('Acme')
        beta = self.partner('Beta', vat='BE0477472701')
        # Odoo derives is_company from the VAT when a partner is created; a manager sets it by hand.
        (acme | beta).is_company = True
        john = self.partner('John')
        out = self._outcomes(rule, acme, beta, john)
        self.assertEqual((out[acme.id].status, out[beta.id].status, out[john.id].status),
                         (FAIL, PASS, NOT_APPLICABLE))
        everyone = self.make_rule('res.partner', 'condition', company=False, condition_domain="[('vat', '!=', False)]")
        out = self._outcomes(everyone, john, beta)
        self.assertEqual((out[john.id].status, out[beta.id].status), (FAIL, PASS))

    def test_cross_field_each_operator(self):
        """FR-DGQ-0008: each operator at lower, equal and greater; an empty field is not applicable."""
        low = self.product('Low', list_price=1, x_dgq_cost=3)
        same = self.product('Same', list_price=3, x_dgq_cost=3)
        high = self.product('High', list_price=5, x_dgq_cost=3)
        rows = {
            '=': (FAIL, PASS, FAIL), '!=': (PASS, FAIL, PASS),
            '>': (FAIL, FAIL, PASS), '>=': (FAIL, PASS, PASS),
            '<': (PASS, FAIL, FAIL), '<=': (PASS, PASS, FAIL),
        }
        for symbol, expected in rows.items():
            with self.subTest(operator=symbol):
                rule = self.make_rule(
                    'product.template', 'cross_field', ['list_price'], company=False,
                    compare_field_id=self.fld('product.template', 'x_dgq_cost').id, compare_operator=symbol)
                out = self._outcomes(rule, low, same, high)
                self.assertEqual((out[low.id].status, out[same.id].status, out[high.id].status), expected)
        failing = self._outcomes(rule, high)[high.id]
        self.assertEqual(failing.status, FAIL)
        rule = self.make_rule(
            'product.template', 'cross_field', ['x_dgq_end'], company=False,
            compare_field_id=self.fld('product.template', 'x_dgq_start').id, compare_operator='>=')
        open_ended = self.product('Open', x_dgq_start='2026-01-10')
        self.assertEqual(self._outcomes(rule, open_ended)[open_ended.id].status, NOT_APPLICABLE)

    def test_cross_field_explanation_quotes_both_values(self):
        """FR-DGQ-0008: the explanation names both fields, both values and the comparison."""
        rule = self.make_rule(
            'product.template', 'cross_field', ['list_price'], company=False,
            compare_field_id=self.fld('product.template', 'x_dgq_cost').id, compare_operator='>=')
        pen = self.product('Pen', list_price=2, x_dgq_cost=3)
        text = self._explain(self._outcomes(rule, pen)[pen.id])
        self.assertIn('2', text)
        self.assertIn('3', text)
        self.assertIn('greater than or equal to', text)

    def test_cross_field_hides_the_values_of_an_excluded_field(self):
        """BR-DGQ-0010: an excluded field's value is not written into the explanation."""
        rule = self.make_rule(
            'product.template', 'cross_field', ['list_price'], company=False,
            compare_field_id=self.fld('product.template', 'x_dgq_cost').id, compare_operator='>=')
        pen = self.product('Pen', list_price=2, x_dgq_cost=3)
        config = self.env['data.quality.field.config']
        with patch.object(type(config), '_get_usable_field_names', return_value={'name'}):
            outcome = self._outcomes(rule, pen)[pen.id]
        self.assertTrue(outcome.params['value_hidden'])
        self.assertNotIn('value', outcome.params)
        self.assertNotIn('3', self._explain(outcome))

    def test_custom_validator_reasons_and_unregistered_key(self):
        """FR-DGQ-0009, BR-DGQ-0009: a registered validator's reason fails the record; an unknown key never runs."""
        self._register('rejects_bad', lambda env, rule, records: {
            r.id: 'No good' for r in records if r.name.startswith('Bad')})
        rule = self.make_rule('res.partner', 'custom', company=False, validator_key='rejects_bad')
        bad, good = self.partner('Bad one'), self.partner('Good one')
        out = self._outcomes(rule, bad, good)
        self.assertEqual(out[bad.id].status, FAIL)
        self.assertEqual(self._explain(out[bad.id]), 'No good')
        self.assertEqual(out[good.id].status, PASS)
        rule.validator_key = 'rejects_bad'
        with patch.object(type(self.env['data.quality.registry']), '_dgq_validators',
                          lambda this: [], create=True):
            out = self._outcomes(rule, bad)
        self.assertEqual(out[bad.id].status, NOT_APPLICABLE)

    def test_custom_validator_error_fails_only_its_record(self):
        """FR-DGQ-0009: a validator that raises fails the record it met; the others keep their result."""
        def handler(env, rule, records):
            if any(r.name == 'Broken' for r in records):
                raise ValueError('exploded')
            return {r.id: 'Nope' for r in records if r.name == 'Wrong'}

        self._register('fragile', handler)
        rule = self.make_rule('res.partner', 'custom', company=False, validator_key='fragile')
        broken, wrong, fine = self.partner('Broken'), self.partner('Wrong'), self.partner('Fine')
        out = self._outcomes(rule, broken, wrong, fine)
        self.assertEqual((out[broken.id].status, out[broken.id].code), (FAIL, 'custom_error'))
        self.assertIn('exploded', self._explain(out[broken.id]))
        self.assertEqual(out[wrong.id].status, FAIL)
        self.assertEqual(out[fine.id].status, PASS)

    def test_validator_for_another_model_is_not_run(self):
        """FR-DGQ-0009: a validator registered for other models is not run on this model."""
        calls = []
        self._register('only_products', lambda env, rule, records: calls.append(1) or {}, models=['product.template'])
        rule = self.make_rule('res.partner', 'custom', company=False, validator_key='only_products')
        partner = self.partner('Any')
        self.assertEqual(self._outcomes(rule, partner)[partner.id].status, NOT_APPLICABLE)
        self.assertFalse(calls)
