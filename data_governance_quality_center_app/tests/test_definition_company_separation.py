from odoo import Command
from odoo.exceptions import AccessError

from .acceptance_m2b_helpers import M2BCase


class TestDefinitionCompanySeparation(M2BCase):
    """Regression tests for B-0208: rule definitions are separated by company.

    A user of company Y neither reads nor changes the validation rules of company X, their kept
    versions, or the matching fields and set-aside pairs of company X's duplicate rules. Rules
    with no company stay shared. The rule list and form are reached through the same search,
    read and write the screens call; the screens themselves are client side.

    Requirements: FR-DGQ-0071, FR-DGQ-0014
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.viewer_y = cls._user_of('viewer', 'dgq_sep_viewer_y', cls.cy)
        cls.manager_y = cls._user_of('manager', 'dgq_sep_manager_y', cls.cy)

    @classmethod
    def _user_of(cls, level, login, company):
        """A user of one access level whose only company is ``company``."""
        return cls.env['res.users'].create({
            'name': login, 'login': login,
            'company_id': company.id, 'company_ids': [Command.set([company.id])],
            'group_ids': [Command.set([
                cls.env.ref('base.group_user').id,
                cls.env.ref('%s.group_dgq_%s' % ('data_governance_quality_center_app', level)).id])],
        })

    def _rule_of_x(self):
        rule = self.make_rule('res.partner', 'required', ['ref'], company=self.cx)
        rule.write({'severity': 'critical'})
        self.env.flush_all()
        return rule

    def test_bug_b_0208_viewer_of_y_does_not_find_rule_of_x(self):
        """B-0208: the rule list of a viewer of Y leaves out company X's rule and refuses its read."""
        rule = self._rule_of_x()
        Rule = self.env['data.quality.rule'].with_user(self.viewer_y)
        self.assertNotIn(rule, Rule.search([]))
        with self.assertRaises(AccessError):
            rule.with_user(self.viewer_y).read(['name'])

    def test_bug_b_0208_manager_of_y_cannot_change_rule_of_x(self):
        """B-0208: a manager of Y can neither edit, archive nor delete company X's rule."""
        rule = self._rule_of_x()
        as_manager = rule.with_user(self.manager_y)
        with self.assertRaises(AccessError):
            as_manager.write({'name': 'Changed by Y'})
        with self.assertRaises(AccessError):
            as_manager.action_archive()
        with self.assertRaises(AccessError):
            as_manager.unlink()

    def test_bug_b_0208_viewer_of_y_does_not_see_versions_of_rule_of_x(self):
        """B-0208: kept versions of company X's rule are hidden from a viewer of Y."""
        rule = self._rule_of_x()
        versions = self.env['data.quality.rule.version'].search([('rule_id', '=', rule.id)])
        self.assertTrue(versions)
        Version = self.env['data.quality.rule.version'].with_user(self.viewer_y)
        self.assertFalse(Version.search([('id', 'in', versions.ids)]))
        with self.assertRaises(AccessError):
            versions.with_user(self.viewer_y).read(['version'])

    def test_bug_b_0208_shared_rule_and_its_versions_stay_visible(self):
        """B-0208 guard: a rule with no company and its versions are visible to a viewer of Y."""
        rule = self.make_rule('res.partner', 'required', ['ref'], company=False)
        rule.write({'severity': 'critical'})
        self.env.flush_all()
        self.assertIn(rule, self.env['data.quality.rule'].with_user(self.viewer_y).search([]))
        versions = self.env['data.quality.rule.version'].with_user(self.viewer_y).search(
            [('rule_id', '=', rule.id)])
        self.assertTrue(versions)

    def test_bug_b_0208_manager_of_y_cannot_reach_matching_fields_of_x(self):
        """B-0208 sibling: matching fields of company X's duplicate rule are out of reach of Y."""
        dup_rule = self.dup_rule('res.partner', [('name', 'fuzzy', 1)], company=self.cx)
        line = dup_rule.field_line_ids
        self.env.flush_all()
        Line = self.env['data.quality.duplicate.rule.field'].with_user(self.manager_y)
        self.assertFalse(Line.search([('id', '=', line.id)]))
        with self.assertRaises(AccessError):
            line.with_user(self.manager_y).write({'weight': 5})
        with self.assertRaises(AccessError):
            line.with_user(self.manager_y).unlink()

    def test_bug_b_0208_manager_of_y_cannot_reach_set_aside_pairs_of_x(self):
        """B-0208 sibling: pairs set aside under company X's duplicate rule are out of reach of Y."""
        dup_rule = self.dup_rule('res.partner', [('name', 'fuzzy', 1)], company=self.cx)
        first, second = self.partner('Pair one'), self.partner('Pair two')
        dismissal = self.env['data.quality.duplicate.dismissal'].create({
            'rule_id': dup_rule.id, 'res_model': 'res.partner',
            'res_id_a': min(first.id, second.id), 'res_id_b': max(first.id, second.id)})
        self.env.flush_all()
        Dismissal = self.env['data.quality.duplicate.dismissal'].with_user(self.manager_y)
        self.assertFalse(Dismissal.search([('id', '=', dismissal.id)]))
        with self.assertRaises(AccessError):
            dismissal.with_user(self.manager_y).unlink()
