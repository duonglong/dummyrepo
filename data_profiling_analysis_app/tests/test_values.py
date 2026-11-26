from odoo.tests import tagged

from .common import DpaCase


@tagged('post_install', '-at_install')
class TestValueLists(DpaCase):
    """Value lists, flags and unused selection values beyond the locked acceptance tests."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.cr.execute("UPDATE res_partner SET active = false")
        cls.env.invalidate_all()
        cls.partner_model = cls.env['ir.model']._get('res.partner')

    def _line(self, field_name, user=None, **settings):
        profile = self.env['dpa.profile'].create({
            'name': 'Values', 'model_id': self.partner_model.id,
            'field_ids': [(6, 0, [self.env['ir.model.fields']._get('res.partner', field_name).id])],
            **settings,
        })
        profile.with_user(user or self.member).action_run()
        report = self.env['dpa.snapshot'].search([('profile_id', '=', profile.id)])
        return report.field_line_ids

    def test_equal_counts_ordered_by_value(self):
        """Requirements: FR-DPA-0007 (equal counts are ordered by value)."""
        self.env['res.partner'].create([{'name': n, 'city': c} for n, c in (('A', 'Zed'), ('B', 'Abe'))])
        values = self._line('city').value_ids.sorted('sequence')
        self.assertEqual(values.mapped('value_label'), ['Abe', 'Zed'])

    def test_long_text_shows_count_only(self):
        """Requirements: FR-DPA-0007 (a long text lists no value)."""
        self.env['res.partner'].create([{'name': 'A', 'comment': 'one'}, {'name': 'B', 'comment': 'two'}])
        line = self._line('comment')
        self.assertEqual(line.distinct_count, 2)
        self.assertFalse(line.value_ids)

    def test_flag_not_set_without_records(self):
        """Requirements: FR-DPA-0005 (no records, no flag)."""
        self.assertFalse(self._line('email').high_empty)

    def test_flag_follows_threshold_of_the_report(self):
        """Requirements: FR-DPA-0005 (1 of 3 empty is above 33%, not above 34%)."""
        self.env['res.partner'].create([{'name': 'A', 'email': 'a@x.com'}, {'name': 'B', 'email': 'b@x.com'}, {'name': 'C'}])
        self.assertTrue(self._line('email', empty_threshold=33.0).high_empty)
        self.assertFalse(self._line('email', empty_threshold=34.0).high_empty)
        self.assertEqual(self._line('email', empty_threshold=33.0).flag_labels, "High empty rate")

    def test_unreadable_linked_record_is_restricted(self):
        """Requirements: FR-DPA-0013, NFR-SEC-0002 (a linked record the runner cannot read is not named)."""
        hidden = self.env['res.users'].create({'name': 'Hidden', 'login': 'dpa_hidden'})
        self.env['res.partner'].create({'name': 'A', 'user_id': hidden.id})
        self.env['ir.access'].create({
            'name': 'dpa test hide user', 'model_id': self.env['ir.model']._get('res.users').id,
            'operation': 'r', 'domain': "[('login', '!=', 'dpa_hidden')]",
        })
        values = self._line('user_id').value_ids
        self.assertEqual(values.sorted('sequence')[0].value_label, "Restricted record")
        self.assertNotIn('Hidden', values.mapped('value_label'))

    def test_selection_without_records_lists_every_value_unused(self):
        """Requirements: FR-DPA-0014 (no record uses any value)."""
        line = self._line('type')
        self.assertIn('Contact', line.unused_values)
        self.assertIn('Other', line.unused_values)
