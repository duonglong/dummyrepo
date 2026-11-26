from odoo.exceptions import AccessError
from odoo.tests import tagged

from .common import DpaCase


@tagged('post_install', '-at_install')
class TestRunEngine(DpaCase):
    """Engine and report behaviour beyond the locked acceptance tests."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.cr.execute("UPDATE res_partner SET active = false")
        cls.env.invalidate_all()
        cls.partner_model = cls.env['ir.model']._get('res.partner')

    def _run(self, field_names, user=None):
        profile = self.env['dpa.profile'].create({
            'name': 'Engine', 'model_id': self.partner_model.id,
            'field_ids': [(6, 0, [self.env['ir.model.fields']._get('res.partner', n).id for n in field_names])],
        })
        profile.with_user(user or self.member).action_run()
        return self.env['dpa.snapshot'].search([('profile_id', '=', profile.id)]), profile

    def test_html_without_text_is_empty(self):
        """Requirements: BR-DPA-0001 (a comment of only tags and spaces counts as empty)."""
        self.env['res.partner'].create([
            {'name': 'A', 'comment': '<p>Note</p>'},
            {'name': 'B', 'comment': '<p> </p>'},
            {'name': 'C'},
        ])
        report, _profile = self._run(['comment'])
        self.assertEqual(report.field_line_ids.filled_count, 1)

    def test_many2many_filled_when_linked(self):
        """Requirements: FR-DPA-0004 (a record with two tags counts once)."""
        tags = self.env['res.partner.category'].create([{'name': 'T1'}, {'name': 'T2'}])
        self.env['res.partner'].create([
            {'name': 'A', 'category_id': [(6, 0, tags.ids)]}, {'name': 'B'}])
        report, _profile = self._run(['category_id'])
        self.assertEqual(report.field_line_ids.filled_count, 1)
        self.assertEqual(report.field_line_ids.completeness, 50.0)

    def test_no_records_gives_zero_completeness(self):
        """Requirements: FR-DPA-0004 (no division by zero on a model without records)."""
        report, _profile = self._run(['email'])
        self.assertEqual(report.record_count, 0)
        self.assertEqual(report.field_line_ids.completeness, 0.0)

    def test_record_rules_limit_the_count(self):
        """Requirements: NFR-SEC-0002, BR-DPA-0006 (a record rule hides records from the count)."""
        self.env['res.partner'].create([{'name': 'Seen'}, {'name': 'Hidden'}])
        self.env['ir.access'].create({
            'name': 'dpa test hide', 'model_id': self.partner_model.id,
            'operation': 'r', 'domain': "[('name', '!=', 'Hidden')]",
        })
        report, _profile = self._run(['email'])
        self.assertEqual(report.record_count, 1)

    def test_runner_is_author_and_companies_kept(self):
        """Requirements: FR-DPA-0003 (the report records its author and the companies counted)."""
        report, _profile = self._run(['email'])
        self.assertEqual(report.user_id, self.member)
        self.assertEqual(report.company_ids, self.member.company_id)
        self.assertFalse(report.is_saved)

    def test_outsider_cannot_run_or_read(self):
        """Requirements: FR-DPA-0032 (no access to profiles or reports outside the group)."""
        report, profile = self._run(['email'])
        with self.assertRaises(AccessError):
            profile.with_user(self.outsider).action_run()
        with self.assertRaises(AccessError):
            report.with_user(self.outsider).read(['name'])
