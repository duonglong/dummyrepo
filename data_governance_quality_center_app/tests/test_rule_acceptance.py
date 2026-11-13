from odoo.exceptions import AccessError, UserError
from odoo.tests import Form

from .acceptance_helpers import AcceptanceCase, dump


class TestRuleAcceptance(AcceptanceCase):
    """Acceptance tests of T-005 (rule model, form, switch, versions, companies).

    Requirements: FR-DGQ-0001, FR-DGQ-0010, FR-DGQ-0011, FR-DGQ-0014
    """

    def _form_rule(self, user, name='Vendor needs VAT'):
        with Form(self.env['data.quality.rule'].with_user(user)) as form:
            form.name = name
            form.model_id = self.model('res.partner')
            form.rule_type = 'required'
            form.field_ids.add(self.fld('res.partner', 'vat'))
            form.severity = 'warning'
        return form.record

    # ---- FR-DGQ-0001 -------------------------------------------------------

    def test_manager_creates_a_rule_from_the_form(self):
        """SC-DGQ-0001-1 (first part): a manager saves the rule; it is active at version 1.

        The part 'the next scan checks it' needs the scan: see test_rule_scan_acceptance.py.
        """
        manager = self.user('manager')
        rule = self._form_rule(manager)
        self.assertTrue(rule.active)
        self.assertEqual(rule.version, 1)
        self.assertEqual(rule.rule_type, 'required')
        self.assertEqual(rule.severity, 'warning')
        self.assertEqual(rule.field_ids, self.fld('res.partner', 'vat'))

    def test_severity_defaults_to_warning_and_has_three_levels(self):
        """FR-DGQ-0001: severity is information, warning or critical; warning by default."""
        Rule = self.env['data.quality.rule']
        self.assertEqual({key for key, _label in Rule._fields['severity'].selection},
                         {'info', 'warning', 'critical'})
        self.assertEqual(Rule.default_get(['severity']).get('severity'), 'warning')

    def test_check_types_offered(self):
        """FR-DGQ-0001: required, pattern, range, date, link, condition, cross-field and custom."""
        selection = self.env['data.quality.rule']._fields['rule_type'].selection
        self.assertEqual({key for key, _label in selection}, {
            'required', 'pattern', 'range', 'date', 'link', 'condition', 'cross_field', 'custom'})

    def test_model_not_marked_supported_is_refused(self):
        """SC-DGQ-0001-2: a model with no 'supported' mark cannot be the model of a rule.

        The offer in the form's list is a view domain of the build; the server refusal is asserted.
        """
        self.assertFalse(self.env['data.quality.model.config'].search(
            [('model_id', '=', self.model('res.currency').id), ('status', '=', 'supported')]))
        with self.assertRaises(UserError):
            self.make_rule('res.currency', 'required', ['name'])

    def test_rule_without_field_is_refused(self):
        """SC-DGQ-0001-3: saving a required rule with no field is refused with a message about the field."""
        with self.assertRaises((UserError, AssertionError)) as caught:
            with Form(self.env['data.quality.rule']) as form:
                form.name = 'No field'
                form.model_id = self.model('res.partner')
                form.rule_type = 'required'
                form.severity = 'warning'
        self.assertIn('field', str(caught.exception).lower())

    def test_condition_rule_needs_no_field(self):
        """FR-DGQ-0001 (design): at least one field, except for condition and custom rules."""
        rule = self.make_rule(
            'res.partner', 'condition', applies_domain="[('is_company', '=', True)]",
            condition_domain="[('vat', '!=', False)]")
        self.assertEqual(rule.version, 1)
        self.assertFalse(rule.field_ids)

    # ---- FR-DGQ-0010 -------------------------------------------------------

    def test_switch_off_keeps_the_rule_and_its_version(self):
        """SC-DGQ-0010-1 (rule part): switching off keeps the rule, its version and its history.

        That the scan leaves it out and keeps its 4 issues is in test_rule_scan_acceptance.py.
        """
        rule = self.make_rule('res.partner', 'required', ['vat'], name='Vendor needs VAT')
        rule.active = False
        self.assertFalse(rule.active)
        found = self.env['data.quality.rule'].with_context(active_test=False).search([('id', '=', rule.id)])
        self.assertEqual(found, rule)
        self.assertEqual(rule.version, 1)
        self.assertFalse(rule.version_ids)

    def test_switch_on_keeps_the_version(self):
        """SC-DGQ-0010-2 (rule part): a switch is not a change of what the rule checks.

        That the next scan checks it again is in test_rule_scan_acceptance.py.
        """
        rule = self.make_rule('res.partner', 'required', ['vat'], active=False)
        rule.active = True
        self.assertTrue(rule.active)
        self.assertEqual(rule.version, 1)
        self.assertFalse(rule.version_ids)

    # ---- FR-DGQ-0011 -------------------------------------------------------

    def _range_rule(self):
        return self.make_rule(
            'product.template', 'range', ['x_dgq_cost'], name='Cost 0 to 10000',
            has_min=True, min_value=0, has_max=True, max_value=10000)

    def test_change_of_the_check_keeps_the_old_version(self):
        """SC-DGQ-0011-1: raising the maximum makes version 2; version 1 is kept read-only."""
        manager = self.user('manager')
        rule = self._range_rule().with_user(manager)
        self.assertEqual(rule.version, 1)
        with Form(rule) as form:
            form.max_value = 20000
        rule = form.record.sudo()
        self.assertEqual(rule.version, 2)
        old = rule.version_ids.filtered(lambda v: v.version == 1)
        self.assertEqual(len(old), 1)
        self.assertIn('10000', dump(old.definition))
        self.assertEqual(old.user_id, manager)
        self.assertTrue(old.date)
        self.assertTrue(old.changed_values)
        with self.assertRaises((UserError, AccessError)):
            old.write({'version': 7})
        with self.assertRaises((UserError, AccessError)):
            old.unlink()

    def test_each_kind_of_change_raises_the_version(self):
        """FR-DGQ-0011: model, check type, fields, settings, filters and severity each make a version."""
        rule = self._range_rule()
        rule.min_value = -5
        self.assertEqual(rule.version, 2)
        rule.severity = 'critical'
        self.assertEqual(rule.version, 3)
        rule.field_ids = [(6, 0, [self.fld('product.template', 'list_price').id])]
        self.assertEqual(rule.version, 4)
        self.assertEqual(sorted(rule.version_ids.mapped('version')), [1, 2, 3])

    def test_name_description_and_advice_do_not_make_a_version(self):
        """SC-DGQ-0011-2: a change of the name only leaves the rule at its version."""
        rule = self._range_rule()
        rule.max_value = 20000
        self.assertEqual(rule.version, 2)
        rule.name = 'Cost, wider'
        rule.description = 'A description'
        rule.advice = 'Ask purchasing'
        self.assertEqual(rule.version, 2)
        self.assertEqual(len(rule.version_ids), 1)

    # ---- FR-DGQ-0014 -------------------------------------------------------

    def test_rule_of_one_company_is_stored_with_that_company(self):
        """SC-DGQ-0014-1 (rule part): a rule for company X holds company X only.

        That its scan leaves the records of company Y out is in test_rule_scan_acceptance.py.
        """
        rule = self.make_rule('res.partner', 'required', ['ref'], company=self.cx)
        self.assertEqual(rule.company_ids, self.cx)

    def test_rule_without_company_holds_no_company(self):
        """SC-DGQ-0014-2 (rule part): a rule with no company has an empty company list."""
        rule = self.make_rule('res.partner', 'required', ['ref'], company=False)
        self.assertFalse(rule.company_ids)
