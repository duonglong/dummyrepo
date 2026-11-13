from odoo.exceptions import UserError
from odoo.tests import new_test_user, tagged

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
    """The groups phase copes with records that disappear while a check runs.

    Requirements: FR-DGQ-0039
    """

    def test_pair_of_a_deleted_record_is_dropped_without_error(self):
        rule = self.env['data.quality.duplicate.rule'].create({
            'name': 'Deleted', 'model_id': self.env['ir.model']._get('res.partner').id,
            'field_line_ids': [(0, 0, {
                'field_id': self.env['ir.model.fields']._get('res.partner', 'name').id,
                'method': 'exact', 'weight': 1})],
        })
        one, two, gone = self.env['res.partner'].create([{'name': 'Acme'}, {'name': 'Acme'}, {'name': 'Acme'}])
        check = self.env['data.quality.duplicate.check'].create({'rule_id': rule.id})
        gone_id = gone.id
        gone.unlink()
        check.write({'phase': 'groups', 'pair_data': [[one.id, two.id, 100.0], [two.id, gone_id, 100.0]]})
        check._run_groups_batch()
        groups = self.env['data.quality.duplicate.group'].search([('rule_id', '=', rule.id)])
        self.assertEqual(len(groups), 1)
        self.assertEqual(set(groups.line_ids.mapped('res_id')), {one.id, two.id})
