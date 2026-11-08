from odoo import Command
from odoo.exceptions import AccessError
from odoo.tests.common import TransactionCase


class TestCompanyRules(TransactionCase):
    """Sets and their lines follow the company of the person reading them.

    Requirements: FR-DRD-0018, BR-DRD-0004
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company_a = cls.env['res.company'].create({'name': 'DRD Company A'})
        cls.company_b = cls.env['res.company'].create({'name': 'DRD Company B'})
        base = cls.env.ref('base.group_user')
        manager_group = cls.env.ref('new_duplicate_record_detector.group_duplicate_manager')
        cls.manager_a = cls.env['res.users'].create({
            'name': 'CR Mgr A', 'login': 'cr_mgr_a',
            'company_id': cls.company_a.id, 'company_ids': [Command.set(cls.company_a.ids)],
            'group_ids': [Command.set((base | manager_group).ids)]})
        rule = cls.env['duplicate.rule'].create({
            'name': 'CR rule', 'model_id': cls.env['ir.model']._get('res.partner').id,
            'condition_ids': [Command.create({
                'field_id': cls.env['ir.model.fields']._get('res.partner', 'email').id,
                'match_type': 'exact'})]})
        cls.group_a, cls.group_b, cls.group_shared = cls.env['duplicate.group'].create([
            cls._group_values(rule, company) for company in (cls.company_a, cls.company_b, cls.env['res.company'])])
        cls.partner = cls.env['res.partner'].create({'name': 'CR Partner'})
        cls.lines = cls.env['duplicate.group.line'].create([
            cls._line_values(group) for group in (cls.group_a, cls.group_b, cls.group_shared)])

    @classmethod
    def _group_values(cls, rule, company):
        return {'rule_id': rule.id, 'model_name': 'res.partner', 'company_id': company.id}

    @classmethod
    def _line_values(cls, group):
        return {'group_id': group.id, 'res_model': 'res.partner', 'res_id': cls.partner.id,
                'name': 'CR Partner', 'company_id': group.company_id.id}

    def test_bug_b_0138_manager_sees_only_sets_of_own_companies(self):
        """Regression test for B-0138: a manager limited to company A sees no set of company B.

        Requirements: FR-DRD-0018, BR-DRD-0004
        """
        groups = self.env['duplicate.group'].with_user(self.manager_a).search([('id', 'in', (
            self.group_a | self.group_b | self.group_shared).ids)])
        self.assertEqual(groups, self.group_a | self.group_shared)

    def test_bug_b_0138_manager_sees_only_lines_of_own_companies(self):
        """Regression test for B-0138: lines of a set of company B stay hidden too.

        Requirements: FR-DRD-0018, BR-DRD-0004
        """
        lines = self.env['duplicate.group.line'].with_user(self.manager_a).search([('id', 'in', self.lines.ids)])
        self.assertEqual(lines.group_id, self.group_a | self.group_shared)

    def test_bug_b_0139_manager_cannot_create_a_set_line(self):
        """Regression test for B-0139: a manager cannot point a line of their own set at any record.

        Requirements: FR-DRD-0018, BR-DRD-0004
        """
        other = self.env['res.partner'].create({'name': 'CR Other'})
        with self.assertRaises(AccessError):
            self.env['duplicate.group.line'].with_user(self.manager_a).create(
                dict(self._line_values(self.group_a), res_id=other.id))

    def test_bug_b_0139_manager_cannot_change_or_delete_a_set_line(self):
        """Regression test for B-0139: existing lines are read only for a manager.

        Requirements: FR-DRD-0018, BR-DRD-0004
        """
        line = self.lines[0].with_user(self.manager_a)
        with self.assertRaises(AccessError):
            line.write({'res_id': self.partner.id})
        with self.assertRaises(AccessError):
            line.unlink()

    def test_bug_b_0139_manager_cannot_delete_a_set(self):
        """Regression test for B-0139: a manager updates sets but never deletes them.

        Requirements: FR-DRD-0018
        """
        with self.assertRaises(AccessError):
            self.group_a.with_user(self.manager_a).unlink()
