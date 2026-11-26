from odoo.exceptions import AccessError
from odoo.tests import tagged

from .common import DpaCase

DPA_MODELS = (
    'dpa.profile', 'dpa.snapshot', 'dpa.snapshot.field', 'dpa.snapshot.value',
    'dpa.snapshot.finding', 'dpa.snapshot.compare', 'dpa.snapshot.compare.line',
)


@tagged('post_install', '-at_install')
class TestPolish(DpaCase):
    """Flags as text and the group-only access sweep."""

    def _report(self, rows, field_names):
        self.env.cr.execute("UPDATE res_partner SET active = false")
        self.env.invalidate_all()
        self.env['res.partner'].create(rows)
        profile = self.env['dpa.profile'].create({
            'name': 'Polish', 'model_id': self.env['ir.model']._get('res.partner').id,
            'empty_threshold': 50,
            'field_ids': [(6, 0, [self.env['ir.model.fields']._get('res.partner', n).id for n in field_names])],
        })
        profile.with_user(self.member).action_run()
        return self.env['dpa.snapshot'].search([('profile_id', '=', profile.id)])

    def test_high_empty_rate_flag_is_a_text_label(self):
        """Requirements: NFR-USE-0001 (the flag is words, not a colour)."""
        report = self._report([{'name': 'A'}, {'name': 'B'}, {'name': 'C', 'email': 'c@x.com'}], ['email'])
        self.assertEqual(report.field_line_ids.flag_labels, "High empty rate")

    def test_unflagged_field_has_no_label(self):
        """Requirements: NFR-USE-0001 (a field below the threshold carries no flag text)."""
        report = self._report([{'name': 'A', 'email': 'a@x.com'}], ['email'])
        self.assertFalse(report.field_line_ids.flag_labels)

    def test_finding_kinds_are_worded(self):
        """Requirements: NFR-USE-0001 (every finding kind has a text label)."""
        kinds = dict(self.env['dpa.snapshot.finding']._fields['kind'].selection)
        self.assertEqual(
            kinds, {'inconsistent_format': "Inconsistent format", 'unusual_number': "Unusual number",
                    'outdated': "Outdated"})

    def test_snapshot_views_do_not_flag_by_colour(self):
        """Requirements: NFR-USE-0001 (no row decoration or colour widget carries a flag)."""
        arch = self.env['dpa.snapshot'].get_view(view_type='form')['arch']
        self.assertNotIn('decoration-', arch)
        self.assertNotIn('badge', arch)
        self.assertIn('flag_labels', arch)

    def test_outsider_is_refused_every_model(self):
        """Requirements: NFR-SEC-0001 (users outside the group have no right on any profiling model)."""
        for model in DPA_MODELS:
            with self.subTest(model=model):
                self.assertFalse(self.env[model].with_user(self.outsider).has_access('read'))
                with self.assertRaises(AccessError):
                    self.env[model].with_user(self.outsider).search([])

    def test_member_has_a_right_on_every_model(self):
        """Requirements: NFR-SEC-0001 (members share every profiling model)."""
        for model in DPA_MODELS:
            with self.subTest(model=model):
                self.assertTrue(self.env[model].with_user(self.member).has_access('read'))

    def test_member_sees_a_profile_of_another_member(self):
        """Requirements: NFR-SEC-0001 (a profile is shared by the whole group)."""
        other = self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'Other Member', 'login': 'dpa_other', 'email': 'o@example.com',
            'group_ids': [(6, 0, (self.env.ref('base.group_user') | self.group).ids)],
        })
        profile = self.env['dpa.profile'].with_user(other).create({
            'name': 'Shared', 'model_id': self.env['ir.model']._get('res.country').id})
        self.assertEqual(self.env['dpa.profile'].with_user(self.member).search([('id', '=', profile.id)]), profile)
