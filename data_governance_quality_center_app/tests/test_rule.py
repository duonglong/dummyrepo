from odoo.exceptions import AccessError, UserError, ValidationError

from .acceptance_helpers import AcceptanceCase


class TestRule(AcceptanceCase):
    """Unit tests of the rule model beyond the acceptance tests.

    Requirements: FR-DGQ-0001, FR-DGQ-0011
    """

    def test_copy_starts_again_at_version_one(self):
        """FR-DGQ-0011: a copy is named '(copy)', at version 1, without versions."""
        rule = self.make_rule('res.partner', 'required', ['vat'], name='Vendor')
        rule.severity = 'critical'
        copy = rule.copy()
        self.assertEqual(copy.name, 'Vendor (copy)')
        self.assertEqual(copy.version, 1)
        self.assertFalse(copy.version_ids)

    def test_same_value_write_makes_no_version(self):
        """FR-DGQ-0011: writing the value a field already has is not a change."""
        rule = self.make_rule('res.partner', 'required', ['vat'])
        rule.severity = 'warning'
        self.assertEqual(rule.version, 1)

    def test_domain_text_is_checked_not_run(self):
        """FR-DGQ-0001: a filter that is not a domain is refused."""
        with self.assertRaises(ValidationError):
            self.make_rule('res.partner', 'condition', condition_domain="[('x_nope', '=', 1)]")

    def test_pattern_must_compile(self):
        """FR-DGQ-0001: a pattern that is not a regular expression is refused."""
        with self.assertRaises(ValidationError):
            self.make_rule('res.partner', 'pattern', ['vat'], pattern='([a-z')

    def test_version_cannot_be_deleted(self):
        """FR-DGQ-0011: a kept version is read-only."""
        rule = self.make_rule('res.partner', 'required', ['vat'])
        rule.severity = 'info'
        with self.assertRaises(UserError):
            rule.version_ids.unlink()

    def test_copy_of_several_rules(self):
        """FR-DGQ-0011: duplicating several rules at once names each copy after its own rule."""
        first = self.make_rule('res.partner', 'required', ['vat'], name='First')
        second = self.make_rule('res.partner', 'required', ['vat'], name='Second')
        copies = (first | second).copy()
        self.assertEqual(copies.mapped('name'), ['First (copy)', 'Second (copy)'])

    def _mark_comment_sensitive(self):
        self.env['data.quality.field.config'].create({
            'field_id': self.fld('res.partner', 'comment').id,
            'status': 'sensitive',
        })

    def test_condition_filter_cannot_read_a_sensitive_field(self):
        """BR-DGQ-0010: a filter on a sensitive field would make pass/fail an oracle on its value."""
        self._mark_comment_sensitive()
        with self.assertRaises(ValidationError):
            self.make_rule('res.partner', 'condition', condition_domain="[('comment', 'ilike', 'x')]")

    def test_compare_field_cannot_be_a_sensitive_field(self):
        """BR-DGQ-0010: the field compared with is held to the same marks as the checked fields."""
        self._mark_comment_sensitive()
        with self.assertRaises(ValidationError):
            self.make_rule(
                'res.partner', 'cross_field', ['name'],
                compare_field_id=self.fld('res.partner', 'comment').id, compare_operator='=')

    def test_version_cannot_be_set_by_hand(self):
        """FR-DGQ-0011: the version number only moves with a change of the definition."""
        rule = self.make_rule('res.partner', 'required', ['vat'])
        with self.assertRaises(UserError):
            rule.write({'version': 5})

    def test_domain_code_is_refused_not_run(self):
        """BR-DGQ-0009: an expression in a filter is refused without being evaluated.

        Run as code, this text would give a valid domain and the rule would be saved; read as plain
        data, it holds a call and is refused.
        """
        with self.assertRaises(ValidationError):
            self.make_rule('res.partner', 'condition', condition_domain="[('name', '=', str(1))]")

    def test_version_cannot_be_set_on_create(self):
        """FR-DGQ-0011: a new rule starts at version 1; the number cannot be given by hand."""
        with self.assertRaises(UserError):
            self.make_rule('res.partner', 'required', ['vat'], version=5)


class TestRuleFilterFields(AcceptanceCase):
    """Every field a rule filter reads, also through links, is held to the field marks.

    Requirements: FR-DGQ-0001, BR-DGQ-0010
    """

    def _mark_comment_sensitive(self):
        self.env['data.quality.field.config'].create({
            'field_id': self.fld('res.partner', 'comment').id,
            'status': 'sensitive',
        })

    def test_filter_path_to_a_sensitive_field_is_refused(self):
        """BR-DGQ-0010: a filter cannot reach a sensitive field through a link."""
        self._mark_comment_sensitive()
        with self.assertRaises(ValidationError):
            self.make_rule(
                'res.partner', 'condition', condition_domain="[('parent_id.comment', 'ilike', 'x')]")

    def test_filter_sub_domain_on_a_sensitive_field_is_refused(self):
        """BR-DGQ-0010: the inner filter of 'any' is held to the marks of the linked model."""
        self._mark_comment_sensitive()
        with self.assertRaises(ValidationError):
            self.make_rule(
                'res.partner', 'condition',
                condition_domain="[('child_ids', 'any', [('comment', 'ilike', 'x')])]")

    def test_filter_path_to_a_bank_account_number_is_refused(self):
        """BR-DGQ-0010: bank account numbers are sensitive by default, also through links."""
        with self.assertRaisesRegex(ValidationError, 'bank_ids'):
            self.make_rule(
                'res.partner', 'condition', applies_domain="[('is_company', '=', True)]",
                condition_domain="[('commercial_partner_id.bank_ids.account_number', '=like', 'FR76%')]")

    def test_filter_on_a_field_that_is_not_stored_is_refused(self):
        """BR-DGQ-0010: a computed field may read a sensitive one, so only stored fields are used."""
        with self.assertRaises(ValidationError):
            self.make_rule(
                'res.partner', 'condition', condition_domain="[('display_name', 'ilike', 'x')]")

    def test_filter_comparing_a_link_to_text_cannot_search_a_sensitive_field(self):
        """BR-DGQ-0010: a link compared to text searches the linked record's name fields.

        Odoo turns ('parent_id', 'ilike', text) into a search on the contact's display name,
        which reads the tax ID; with the tax ID marked sensitive the filter is refused.
        """
        self.env['data.quality.field.config'].create({
            'field_id': self.fld('res.partner', 'vat').id,
            'status': 'sensitive',
        })
        with self.assertRaises(ValidationError):
            self.make_rule(
                'res.partner', 'condition', condition_domain="[('parent_id', 'ilike', 'FR12')]")

    def test_filter_path_into_an_unmarked_model_is_refused(self):
        """FR-DGQ-0001: a filter may follow links only into models marked supported or restricted."""
        self.assertFalse(self.env['data.quality.model.config'].search(
            [('model_id', '=', self.model('res.company').id)]))
        with self.assertRaisesRegex(ValidationError, 'company_id'):
            self.make_rule(
                'res.partner', 'condition', condition_domain="[('company_id.name', '=', 'X')]")

    def test_manager_saves_a_filter_following_a_link(self):
        """FR-DGQ-0001: a manager who is not a settings administrator saves a linked filter."""
        manager = self.user('manager')
        rule = self.env['data.quality.rule'].with_user(manager).create({
            'name': 'Parent is a company',
            'model_id': self.model('res.partner').id,
            'rule_type': 'condition',
            'condition_domain': "[('parent_id.is_company', '=', True)]",
        })
        self.assertTrue(rule.exists())

    def test_filter_on_a_link_to_an_unmarked_model_by_id_is_saved(self):
        """FR-DGQ-0001: comparing a link with record ids reads nothing of the linked model."""
        rule = self.make_rule(
            'res.partner', 'condition', condition_domain="[('company_id', '=', %d)]" % self.cx.id)
        self.assertTrue(rule.exists())

    def test_filter_path_to_a_usable_field_is_saved(self):
        """FR-DGQ-0001: a filter following a link to usable fields is accepted."""
        rule = self.make_rule(
            'res.partner', 'condition',
            condition_domain="[('parent_id.is_company', '=', True), "
                             "('child_ids', 'any', [('vat', '!=', False)])]")
        self.assertTrue(rule.exists())


class TestRuleFieldListAccess(AcceptanceCase):
    """A viewer reads the field list of marked models only.

    Requirements: FR-DGQ-0001
    """

    def test_viewer_reads_fields_of_a_marked_model(self):
        """FR-DGQ-0001: the rule form shows the fields of the models a rule can check."""
        viewer = self.user('viewer')
        field = self.fld('res.partner', 'vat').with_user(viewer)
        self.assertEqual(field.name, 'vat')

    def test_viewer_cannot_read_fields_of_an_unmarked_model(self):
        """FR-DGQ-0001: fields of models not marked supported or restricted stay hidden."""
        viewer = self.user('viewer')
        self.assertFalse(self.env['data.quality.model.config'].search(
            [('model_id', '=', self.model('res.currency').id)]))
        field = self.fld('res.currency', 'name').with_user(viewer)
        with self.assertRaises(AccessError):
            field.read(['name'])
