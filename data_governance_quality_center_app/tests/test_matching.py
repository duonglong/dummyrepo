from odoo.exceptions import UserError, ValidationError
from odoo.tests import Form, new_test_user, tagged

from ..services import matching
from .common import DGQTestCase


@tagged('post_install', '-at_install')
class TestMatchingService(DGQTestCase):
    """Pure scoring and grouping functions of the duplicate checks.

    Requirements: BR-DGQ-0012, FR-DGQ-0039
    """

    def test_normalize_ignores_case_accents_spaces_and_punctuation(self):
        """BR-DGQ-0012: text is compared in a plain form."""
        self.assertEqual(matching.normalize('  Café,  NOIR! '), 'cafe noir')

    def test_normalize_email_keeps_punctuation_and_phone_keeps_digits(self):
        """BR-DGQ-0012: e-mails in lower case, phones as digits only."""
        self.assertEqual(matching.normalize(' Info@Acme.COM ', 'email'), 'info@acme.com')
        self.assertEqual(matching.normalize('+1 (555) 010', 'phone'), '1555010')

    def test_field_score_methods(self):
        """BR-DGQ-0012: exact is as stored, normalized ignores form, fuzzy is a ratio, empty is None."""
        self.assertEqual(matching.field_score('exact', 'Acme', 'acme'), 0.0)
        self.assertEqual(matching.field_score('normalized', 'Acme', 'acme'), 100.0)
        self.assertEqual(matching.field_score('fuzzy', 'abcd', 'abcd'), 100.0)
        self.assertTrue(0 < matching.field_score('fuzzy', 'Acme Limted', 'Acme Ltd') < 100)
        self.assertIsNone(matching.field_score('exact', 'Acme', False))
        self.assertIsNone(matching.field_score('exact', '  ', 'Acme'))

    def test_pair_score_example_of_the_spec(self):
        """BR-DGQ-0012: a field scoring 0 (weight 2) and one scoring 100 (weight 3) give 60; no field filled gives None."""
        lines = [('email', 'normalized', 3), ('city', 'exact', 2)]
        self.assertEqual(matching.pair_score(lines, {'email': 'A@b.c'}, {'email': 'a@b.c', 'city': 'x'}), 100.0)
        self.assertIsNone(matching.pair_score(lines, {}, {'email': 'a@b.c'}))
        self.assertAlmostEqual(matching.pair_score(
            [('n', 'exact', 2), ('e', 'exact', 3)], {'n': 'a', 'e': 'x'}, {'n': 'b', 'e': 'x'}), 60.0)

    def test_group_pairs_joins_chains(self):
        """FR-DGQ-0039: records linked through other records are one group, with the best score."""
        groups = matching.group_pairs([(1, 2, 90.0), (2, 3, 95.0), (7, 8, 88.0)])
        self.assertEqual(sorted(groups), [([1, 2, 3], 95.0), ([7, 8], 88.0)])

    def test_block_keys_share_the_key_of_alike_values(self):
        """FR-DGQ-0039: alike values share a blocking key; empty values give none."""
        lines = [('name', 'fuzzy', 2), ('email', 'normalized', 3)]
        one = matching.block_keys(lines, {'name': 'Acme Ltd', 'email': 'A@x.com'})
        two = matching.block_keys(lines, {'name': 'ACME Limted', 'email': False})
        self.assertTrue(one & two)
        self.assertFalse(matching.block_keys(lines, {'name': '', 'email': None}))


@tagged('post_install', '-at_install')
class TestDuplicateCheckOverlap(DGQTestCase):
    """A rule never has two checks queued or running.

    Requirements: FR-DGQ-0039
    """

    def test_second_check_of_a_rule_is_refused_while_one_is_queued(self):
        rule = self.env['data.quality.duplicate.rule'].create({
            'name': 'Overlap', 'model_id': self.env['ir.model']._get('res.partner').id,
            'field_line_ids': [(0, 0, {
                'field_id': self.env['ir.model.fields']._get('res.partner', 'name').id,
                'method': 'exact', 'weight': 1})],
        })
        Check = self.env['data.quality.duplicate.check']
        first = Check.create({'rule_id': rule.id})
        first.action_start()
        second = Check.create({'rule_id': rule.id})
        with self.assertRaises(UserError):
            second.action_start()


@tagged('post_install', '-at_install')
class TestDuplicateGroupAccess(DGQTestCase):
    """Duplicate groups are result rows: a viewer reads them with the readable-model filter.

    Requirements: FR-DGQ-0039, BR-DGQ-0011
    """

    def test_viewer_searches_groups_when_two_result_models_exist(self):
        viewer = new_test_user(self.env, login='dgq_dup_viewer',
                               groups='base.group_user,data_governance_quality_center_app.group_dgq_viewer')
        groups = self.env['data.quality.duplicate.group'].with_user(viewer)
        self.assertFalse(groups.search([]))
        self.assertIn('res.partner', groups._get_readable_model_names())
        self.assertNotIn('data.quality.duplicate.group', groups._get_readable_model_names())


@tagged('post_install', '-at_install')
class TestDuplicateGroupsPhase(DGQTestCase):
    """The groups phase copes with records that change while a check runs.

    Each test runs the keys and pairs phases of the check, changes a record of a scored pair,
    then runs the groups phase.

    Requirements: FR-DGQ-0039
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.rule = cls.env['data.quality.duplicate.rule'].create({
            'name': 'Changed while checking', 'model_id': cls.env['ir.model']._get('res.partner').id,
            'domain': "[('name', '=like', 'Zyqx%')]",
            'field_line_ids': [(0, 0, {
                'field_id': cls.env['ir.model.fields']._get('res.partner', 'name').id,
                'method': 'exact', 'weight': 1})],
        })
        cls.one, cls.two, cls.three = cls.env['res.partner'].create([{'name': 'Zyqx Acme'}] * 3)

    def _run_until_groups_phase(self):
        check = self.env['data.quality.duplicate.check'].create({'rule_id': self.rule.id})
        while check.phase != 'groups':
            check._dgq_run_batch()
        return check

    def _grouped_ids(self):
        groups = self.env['data.quality.duplicate.group'].search([('rule_id', '=', self.rule.id)])
        return [set(group.line_ids.mapped('res_id')) for group in groups]

    def test_pair_of_a_deleted_record_is_dropped_without_error(self):
        check = self._run_until_groups_phase()
        self.three.unlink()
        check._dgq_run_batch()
        self.assertEqual(self._grouped_ids(), [{self.one.id, self.two.id}])

    def test_record_archived_during_the_check_is_in_no_group(self):
        check = self._run_until_groups_phase()
        self.three.action_archive()
        check._dgq_run_batch()
        self.assertEqual(self._grouped_ids(), [{self.one.id, self.two.id}])

    def test_record_moved_to_another_company_during_the_check_is_in_no_group(self):
        check = self._run_until_groups_phase()
        other = self.env['res.company'].create({'name': 'Zyqx Other Company'})
        self.three.company_id = other
        check._dgq_run_batch()
        self.assertEqual(self._grouped_ids(), [{self.one.id, self.two.id}])


@tagged('post_install', '-at_install')
class TestDuplicateRuleFieldLines(DGQTestCase):
    """A duplicate rule always keeps at least one matching field.

    Requirements: FR-DGQ-0038
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.name_field = cls.env['ir.model.fields']._get('res.partner', 'name')
        cls.email_field = cls.env['ir.model.fields']._get('res.partner', 'email')
        cls.rule = cls.env['data.quality.duplicate.rule'].create({
            'name': 'Lines', 'model_id': cls.env['ir.model']._get('res.partner').id,
            'field_line_ids': [(0, 0, {'field_id': cls.name_field.id, 'method': 'exact', 'weight': 1})],
        })

    def test_deleting_the_last_field_line_directly_is_refused(self):
        with self.assertRaises(ValidationError):
            self.rule.field_line_ids.unlink()

    def test_the_only_field_can_be_replaced_in_one_save(self):
        """The person removes the line and adds another in the rule form, then saves."""
        with Form(self.rule) as form:
            form.field_line_ids.remove(0)
            with form.field_line_ids.new() as line:
                line.field_id = self.email_field
                line.method = 'normalized'
                line.weight = 3
        self.assertEqual(self.rule.field_line_ids.field_id, self.email_field)

    def test_rule_with_its_lines_can_be_deleted(self):
        self.rule.unlink()
        self.assertFalse(self.rule.exists())


@tagged('post_install', '-at_install')
class TestDuplicateBlockKeys(DGQTestCase):
    """Blocking keys always fit the index of the key work table.

    Requirements: FR-DGQ-0039
    """

    def test_long_key_is_stored_hashed_and_short_key_as_is(self):
        Check = self.env['data.quality.duplicate.check']
        self.assertEqual(Check._stored_block_key(3, 'name=acme'), '3|name=acme')
        long_key = Check._stored_block_key(3, 'comment=' + 'x' * 5000)
        self.assertLess(len(long_key), 100)
        self.assertEqual(long_key, Check._stored_block_key(3, 'comment=' + 'x' * 5000))


@tagged('post_install', '-at_install')
class TestDuplicateCheckCompany(DGQTestCase):
    """A manager sees only the checks of the rules of his companies.

    Requirements: FR-DGQ-0039, BR-DGQ-0011
    """

    def test_manager_does_not_see_the_check_of_another_company_rule(self):
        own, other = self.env['res.company'].create([{'name': 'Zyqx Own'}, {'name': 'Zyqx Other'}])
        manager = new_test_user(
            self.env, login='dgq_dup_check_manager', company_id=own.id, company_ids=[own.id],
            groups='base.group_user,data_governance_quality_center_app.group_dgq_manager')
        name_field = self.env['ir.model.fields']._get('res.partner', 'name')
        rules = self.env['data.quality.duplicate.rule'].create([{
            'name': company.name, 'company_id': company.id,
            'model_id': self.env['ir.model']._get('res.partner').id,
            'field_line_ids': [(0, 0, {'field_id': name_field.id, 'method': 'exact', 'weight': 1})],
        } for company in (own, other)])
        checks = self.env['data.quality.duplicate.check'].create([{'rule_id': rule.id} for rule in rules])
        visible = self.env['data.quality.duplicate.check'].with_user(manager).search([('id', 'in', checks.ids)])
        self.assertEqual(visible, checks[0])
