from odoo.exceptions import AccessError, ValidationError

from .acceptance_m2b_helpers import M2BCase, call_first, DISMISS
from ..services import merge


class TestDuplicateCompare(M2BCase):
    """Own tests of T-017: comparison table, one record set aside, preview figures.

    The buttons themselves (Merge, Not Duplicates, the row button) are pressed by the screen
    review: the tests call the methods the buttons call.

    Requirements: FR-DGQ-0040, FR-DGQ-0041, FR-DGQ-0042
    """

    def setUp(self):
        super().setUp()
        self.rule = self.contact_rule()
        self.acme = self.partner('Acme Ltd', email='info@acme.com')
        self.acme_two = self.partner('ACME Ltd.', email='INFO@acme.com')
        self.acme.phone = '555-0100'
        self.acme_two.phone = '555-0199'
        self.group = self.make_group(self.rule, self.acme | self.acme_two)

    def _bank(self, partner, number):
        """A linked document of a contact: a bank account (the module has no orders)."""
        return self.env['res.partner.bank'].create({'account_number': number, 'partner_id': partner.id})

    def test_comparison_marks_differences_and_shows_links(self):
        """FR-DGQ-0040: the table has both names, marks the phone row and counts linked documents."""
        self._bank(self.acme, 'CMP-1')
        group = self.make_group(self.rule, self.acme | self.acme_two)
        html = str(group.comparison)
        self.assertIn('Acme Ltd', html)
        self.assertIn('555-0199', html)
        self.assertIn('table-warning', html)
        self.assertIn('1 linked documents', html)

    def test_matching_fields_come_before_the_others(self):
        """FR-DGQ-0040: name and e-mail rows are first."""
        rows = merge.field_rows(
            self.env, 'res.partner', [self.acme.id, self.acme_two.id], self.rule._get_match_lines())
        self.assertEqual({row['name'] for row in rows[:2]}, {'name', 'email'})
        self.assertTrue(all(row['is_match'] for row in rows[:2]))
        self.assertFalse(any(row['is_match'] for row in rows[2:]))

    def test_sensitive_fields_are_left_out(self):
        """FR-DGQ-0040, BR-DGQ-0010: no password-like or binary field is compared."""
        names = merge.comparable_fields(self.env, 'res.partner')
        self.assertNotIn('image_1920', names)
        self.assertIn('phone', names)

    def test_one_record_set_aside_leaves_the_group_and_closes_a_pair(self):
        """FR-DGQ-0041: setting aside one record of a pair closes the group and remembers the pair."""
        self.group.line_ids[:1].with_user(self.manager_a).action_dismiss()
        self.group.invalidate_recordset()
        self.assertEqual(self.group.state, 'dismissed')
        self.assertEqual(self.env['data.quality.duplicate.dismissal'].sudo().search_count(
            [('rule_id', '=', self.rule.id)]), 1)

    def test_one_record_set_aside_of_three_keeps_the_others_grouped(self):
        """FR-DGQ-0041: with 3 records the 2 others stay in an open group."""
        third = self.partner('Acme Limited', email='info@acme.com')
        group = self.make_group(self.rule, self.acme | self.acme_two | third)
        line = group.line_ids.filtered(lambda l: l.res_id == third.id)
        line.with_user(self.manager_a).action_dismiss()
        group.invalidate_recordset()
        self.assertEqual(group.state, 'open')
        self.assertEqual(self.group_ids(group), {self.acme.id, self.acme_two.id})
        self.assertEqual(self.env['data.quality.duplicate.dismissal'].sudo().search_count(
            [('rule_id', '=', self.rule.id)]), 2)

    def test_set_aside_is_for_managers_only(self):
        """FR-DGQ-0041: an operator is refused."""
        operator = self.user('operator', login='dgq_cmp_op', company=self.cx)
        with self.assertRaises(AccessError):
            call_first(self.group.with_user(operator), DISMISS)

    def test_merge_button_opens_the_preview_with_the_group_records(self):
        """FR-DGQ-0042: the action carries the group and its record ids."""
        action = self.group.with_user(self.manager_a).action_merge()
        self.assertEqual(action['res_model'], 'data.quality.merge.wizard')
        self.assertEqual(
            set(action['context']['default_res_ids']), {self.acme.id, self.acme_two.id})

    def test_unique_collision_makes_the_merge_irreversible(self):
        """FR-DGQ-0042, BR-DGQ-0008: the same bank account number on both contacts cannot be moved."""
        self._bank(self.acme, 'SAME-1')
        self._bank(self.acme_two, 'SAME-1')
        wizard = self.open_preview(group=self.group)
        self.assertFalse(wizard.reversible)
        self.assertTrue(wizard.irreversible_reason)

    def test_kept_record_and_sources_must_be_among_the_records(self):
        """FR-DGQ-0042: a record outside the group cannot be kept."""
        other = self.partner('Somebody Else')
        wizard = self.open_preview(group=self.group)
        with self.assertRaises(ValidationError):
            wizard.kept_res_id = other.id
