from lxml import etree

from odoo import Command
from odoo.exceptions import AccessError
from odoo.tests.common import TransactionCase
from odoo.tools.safe_eval import safe_eval


class TestFullCheckAcceptance(TransactionCase):
    """Acceptance tests of T-003 (full check, duplicates list, not a duplicate).

    Requirements: FR-DRD-0005, FR-DRD-0006, FR-DRD-0007, FR-DRD-0008.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        mgr_group = cls.env['res.groups'].search(
            [('name', 'ilike', 'duplicate manager')], limit=1)
        base = cls.env.ref('base.group_user') | cls.env.ref('base.group_partner_manager')
        cls.manager = cls.env['res.users'].create({
            'name': 'Dup Mgr T003', 'login': 'dup_mgr_t003',
            'group_ids': [Command.set((base | mgr_group).ids)],
        })
        cls.plain = cls.env['res.users'].create({
            'name': 'Plain T003', 'login': 'plain_t003',
            'group_ids': [Command.set(base.ids)],
        })
        cls.P = cls.env['res.partner'].sudo()

    # helpers
    def _rule(self, conditions, name='T003 rule', model='res.partner'):
        return self.env['duplicate.rule'].sudo().create({
            'name': name,
            'model_id': self.env['ir.model']._get(model).id,
            'condition_ids': [Command.create({
                'field_id': self.env['ir.model.fields']._get(model, f).id,
                'match_type': m,
            }) for f, m in conditions],
        })

    def _groups(self, rule, state='open'):
        self.env.flush_all()
        dom = [('rule_id', '=', rule.id)]
        if state:
            dom.append(('state', '=', state))
        return self.env['duplicate.group'].sudo().search(dom)

    def _members(self, group):
        return set(group.line_ids.mapped('res_id'))

    def _run_check(self, rules, user=None):
        """Ask for the full check as a manager, then let the module's cron job run."""
        rules = rules.with_user(user or self.manager)
        for rule in rules:
            rule.action_full_check()
        self.env.flush_all()
        crons = self.env['ir.cron'].sudo().with_context(active_test=False).search([]).filtered(
            lambda c: c.model_id.model.startswith('duplicate.'))
        self.assertTrue(crons, "a cron job of the module must run the full check")
        for cron in crons:
            cron.method_direct_trigger()
        self.env.flush_all()
        rules.invalidate_recordset()

    def _bus_mark(self):
        return self.env['bus.bus'].sudo().search([], order='id desc', limit=1).id or 0

    def _warned(self, mark, *needles):
        return any(all(n in m.message for n in needles)
                   for m in self.env['bus.bus'].sudo().search([('id', '>', mark)]))

    # FR-DRD-0005
    def test_sc_0005_1_full_check_records_one_set_per_group(self):
        """Requirements: FR-DRD-0005. Scenario SC-DRD-0005-1: 3 + 2 contacts, 2 sets."""
        a = [self.P.create({'name': f'FA{i}', 'email': 'a@x-t003.com'}) for i in range(3)]
        b = [self.P.create({'name': f'FB{i}', 'email': 'b@x-t003.com'}) for i in range(2)]
        self.P.create({'name': 'FSingle', 'email': 'c@x-t003.com'})
        rule = self._rule([('email', 'exact')])
        self._run_check(rule)
        sets = self._groups(rule)
        self.assertEqual(len(sets), 2)
        self.assertEqual(sorted(len(self._members(s)) for s in sets), [2, 3])
        self.assertIn({r.id for r in a}, [self._members(s) for s in sets])
        self.assertIn({r.id for r in b}, [self._members(s) for s in sets])
        rule = rule.sudo()
        self.assertEqual(rule.check_state, 'done')
        self.assertEqual(rule.last_check_group_count, 2)
        self.assertTrue(rule.last_check_date)

    def test_check_queued_before_the_job_runs(self):
        """Requirements: FR-DRD-0005. Asking for a check queues it for the asker."""
        rule = self._rule([('email', 'exact')])
        rule.with_user(self.manager).action_full_check()
        rule.invalidate_recordset()
        self.assertEqual(rule.sudo().check_state, 'queued')
        self.assertEqual(rule.sudo().check_user_id, self.manager)

    def test_sc_0005_2_no_match_gives_zero_sets(self):
        """Requirements: FR-DRD-0005. Scenario SC-DRD-0005-2."""
        self.P.create({'name': 'Z1', 'email': 'z1@none-t003.com'})
        self.P.create({'name': 'Z2', 'email': 'z2@none-t003.com'})
        rule = self._rule([('email', 'exact')])
        self._run_check(rule)
        self.assertFalse(self._groups(rule, state=False))
        self.assertEqual(rule.sudo().check_state, 'done')
        self.assertEqual(rule.sudo().last_check_group_count, 0)

    def test_sc_0005_3_companies_never_in_one_set(self):
        """Requirements: FR-DRD-0005, BR-DRD-0004. Scenario SC-DRD-0005-3."""
        co_a = self.env['res.company'].create({'name': 'T003 Co A'})
        co_b = self.env['res.company'].create({'name': 'T003 Co B'})
        a = self.P.create({'name': 'Acme T003', 'company_id': co_a.id})
        b = self.P.create({'name': 'Acme T003', 'company_id': co_b.id})
        rule = self._rule([('name', 'exact')])
        self._run_check(rule)
        self.assertEqual(rule.sudo().check_state, 'done')
        for s in self._groups(rule, state=False):
            self.assertFalse({a.id, b.id} <= self._members(s))

    def test_archived_records_left_out(self):
        """Requirements: FR-DRD-0005, BR-DRD-0004. Archived records are not reported."""
        self.P.create({'name': 'Ar1', 'email': 'ar@arch-t003.com', 'active': False})
        self.P.create({'name': 'Ar2', 'email': 'ar@arch-t003.com'})
        rule = self._rule([('email', 'exact')])
        self._run_check(rule)
        self.assertEqual(rule.sudo().check_state, 'done')
        self.assertFalse(self._groups(rule, state=False))

    def test_non_manager_refused(self):
        """Requirements: FR-DRD-0018, FR-DRD-0005. A plain user cannot run a full check."""
        rule = self._rule([('email', 'exact')])
        with self.assertRaises(AccessError):
            rule.with_user(self.plain).action_full_check()

    # FR-DRD-0006
    def test_sc_0006_1_second_run_does_not_double_sets(self):
        """Requirements: FR-DRD-0006. Scenario SC-DRD-0006-1."""
        for i in range(2):
            self.P.create({'name': f'RA{i}', 'email': 'ra@rep-t003.com'})
            self.P.create({'name': f'RB{i}', 'email': 'rb@rep-t003.com'})
        rule = self._rule([('email', 'exact')])
        self._run_check(rule)
        self.assertEqual(len(self._groups(rule)), 2)
        self._run_check(rule)
        self.assertEqual(len(self._groups(rule)), 2)
        self.assertEqual(rule.sudo().last_check_group_count, 2)

    def test_second_run_replaces_stale_open_sets(self):
        """Requirements: FR-DRD-0006. An open set that no longer matches disappears."""
        a = self.P.create({'name': 'SA', 'email': 'st@stale-t003.com'})
        self.P.create({'name': 'SB', 'email': 'st@stale-t003.com'})
        rule = self._rule([('email', 'exact')])
        self._run_check(rule)
        self.assertEqual(len(self._groups(rule)), 1)
        a.write({'email': 'other@stale-t003.com'})
        self._run_check(rule)
        self.assertFalse(self._groups(rule))

    def test_merged_sets_and_other_rules_are_kept(self):
        """Requirements: FR-DRD-0006. Merged sets stay; sets of other rules are not touched."""
        r_other = self._rule([('name', 'exact')], name='T003 other')
        self.P.create({'name': 'Keep Me T003', 'email': 'k1@keep-t003.com'})
        self.P.create({'name': 'Keep Me T003', 'email': 'k2@keep-t003.com'})
        other_set = self._groups(r_other)
        self.assertEqual(len(other_set), 1)
        for i in range(2):
            self.P.create({'name': f'MG{i}', 'email': 'mg@merged-t003.com'})
        rule = self._rule([('email', 'exact')])
        self._run_check(rule)
        merged = self._groups(rule)
        self.assertEqual(len(merged), 1)
        merged.write({'state': 'merged'})
        self._run_check(rule)
        self.assertEqual(self._groups(rule, 'merged'), merged)
        self.assertFalse(self._groups(rule))
        self.assertEqual(self._groups(r_other), other_set)

    def test_sc_0006_2_not_duplicate_not_reopened(self):
        """Requirements: FR-DRD-0006, FR-DRD-0008. Scenario SC-DRD-0006-2."""
        a = self.P.create({'name': 'NDA', 'email': 'nd@nd-t003.com'})
        b = self.P.create({'name': 'NDB', 'email': 'nd@nd-t003.com'})
        rule = self._rule([('email', 'exact')])
        self._run_check(rule)
        sets = self._groups(rule)
        self.assertEqual(len(sets), 1)
        self.assertEqual(self._members(sets), {a.id, b.id})
        sets.write({'state': 'not_duplicate'})
        self._run_check(rule)
        for s in self._groups(rule):
            self.assertFalse({a.id, b.id} <= self._members(s))
        self.assertEqual(self._groups(rule, 'not_duplicate'), sets)

    # FR-DRD-0007
    def _action(self):
        return self.env['ir.actions.act_window'].sudo().search(
            [('res_model', '=', 'duplicate.group')], limit=1)

    def _default_shown(self):
        """Open the duplicates list as its action does and return the records shown."""
        action = self._action()
        self.assertTrue(action, "the duplicates list needs a window action")
        Group = self.env['duplicate.group'].with_user(self.manager)
        domain = safe_eval(action.domain or '[]')
        ctx = safe_eval(action.context or '{}')
        arch = Group.get_view(view_type='search')['arch']
        tree = etree.fromstring(arch)
        for key, val in ctx.items():
            if key.startswith('search_default_') and val:
                name = key[len('search_default_'):]
                for flt in tree.xpath(f"//filter[@name='{name}']"):
                    if flt.get('domain'):
                        domain = domain + safe_eval(flt.get('domain'))
        return Group.search(domain)

    def test_sc_0007_1_list_filters_on_record_type(self):
        """Requirements: FR-DRD-0007. Scenario SC-DRD-0007-1."""
        r_mail = self._rule([('email', 'exact')], name='T003 Same e-mail')
        r_code = self._rule([('default_code', 'exact')], name='T003 Same barcode',
                            model='product.template')
        for i in range(2):
            self.P.create({'name': f'LP{i}', 'email': 'l@list-t003.com'})
            self.env['product.template'].sudo().create(
                {'name': f'LProd{i}', 'default_code': 'L-1-T003'})
        group = self._groups(r_code)
        self.assertEqual(len(group), 1)
        shown = self._default_shown()
        self.assertIn(group, shown)
        self.assertIn(self._groups(r_mail), shown)
        self.assertEqual(group.rule_id, r_code)
        self.assertEqual(group.model_name, 'product.template')
        self.assertEqual(group.record_count, 2)
        self.assertIn('LProd0', group.record_names)
        self.assertIn('LProd1', group.record_names)
        only_products = self.env['duplicate.group'].with_user(self.manager).search(
            [('model_name', '=', 'product.template'), ('state', '=', 'open')])
        self.assertIn(group, only_products)
        self.assertNotIn(self._groups(r_mail), only_products)

    def test_sc_0007_2_merged_sets_hidden_by_default(self):
        """Requirements: FR-DRD-0007. Scenario SC-DRD-0007-2."""
        r1 = self._rule([('email', 'exact')], name='T003 merged rule')
        r2 = self._rule([('name', 'exact')], name='T003 open rule')
        for i in range(2):
            self.P.create({'name': 'OpenName T003', 'email': f'm{i}@hid-t003.com'})
        for i in range(2):
            self.P.create({'name': f'Mrg{i}', 'email': 'mrg@hid-t003.com'})
        merged = self._groups(r1)
        self.assertEqual(len(merged), 1)
        merged.write({'state': 'merged'})
        open_set = self._groups(r2)
        self.assertEqual(len(open_set), 1)
        shown = self._default_shown()
        self.assertIn(open_set, shown)
        self.assertNotIn(merged, shown)

    def test_list_menu_for_managers_only(self):
        """Requirements: FR-DRD-0007, FR-DRD-0018. Only managers see the duplicates menu."""
        action = self._action()
        self.assertTrue(action, "the duplicates list needs a window action")
        menu = self.env['ir.ui.menu'].sudo().search(
            [('action', '=', f'ir.actions.act_window,{action.id}')], limit=1)
        self.assertTrue(menu, "a menu must open the duplicates list")
        mgr_visible = self.env['ir.ui.menu'].with_user(self.manager)._visible_menu_ids()
        plain_visible = self.env['ir.ui.menu'].with_user(self.plain)._visible_menu_ids()
        self.assertIn(menu.id, mgr_visible)
        self.assertNotIn(menu.id, plain_visible)

    def test_plain_user_cannot_read_sets(self):
        """Requirements: FR-DRD-0007. Ordinary users do not see the list."""
        with self.assertRaises(AccessError):
            self.env['duplicate.group'].with_user(self.plain).search([])

    # FR-DRD-0008
    def test_sc_0008_1_not_a_duplicate_closes_set_and_silences_warning(self):
        """Requirements: FR-DRD-0008. Scenario SC-DRD-0008-1."""
        rule = self._rule([('email', 'exact')], name='T003 Same e-mail 1')
        a = self.P.create({'name': 'Smith Alpha', 'email': 'js@js-t003.com'})
        self.P.create({'name': 'Smith Bravo', 'email': 'js@js-t003.com'})
        sets = self._groups(rule)
        self.assertEqual(len(sets), 1)
        sets.with_user(self.manager).write({'state': 'not_duplicate'})
        self.assertFalse(self._groups(rule))
        mark = self._bus_mark()
        a.with_user(self.manager).write({'email': 'js@js-t003.com'})
        self.assertFalse(self._warned(mark, 'Smith Bravo'))
        self.assertFalse(self._groups(rule))

    def test_not_a_duplicate_refused_to_plain_user(self):
        """Requirements: FR-DRD-0008. Only managers decide."""
        rule = self._rule([('email', 'exact')])
        self.P.create({'name': 'PA', 'email': 'p@pl-t003.com'})
        self.P.create({'name': 'PB', 'email': 'p@pl-t003.com'})
        with self.assertRaises(AccessError):
            self._groups(rule).with_user(self.plain).write({'state': 'not_duplicate'})

    def test_sc_0008_2_new_record_forms_new_set_with_both(self):
        """Requirements: FR-DRD-0008. Scenario SC-DRD-0008-2."""
        rule = self._rule([('email', 'exact')], name='T003 Same e-mail 2')
        a = self.P.create({'name': 'Smith Alpha', 'email': 'sc@sc-t003.com'})
        b = self.P.create({'name': 'Smith Bravo', 'email': 'sc@sc-t003.com'})
        self._groups(rule).write({'state': 'not_duplicate'})
        mark = self._bus_mark()
        c = self.env['res.partner'].with_user(self.manager).create(
            {'name': 'Smith Charlie', 'email': 'sc@sc-t003.com'})
        self.assertTrue(self._warned(mark, 'Smith Alpha', 'Smith Bravo'))
        sets = self._groups(rule)
        self.assertEqual(len(sets), 1)
        self.assertEqual(self._members(sets), {a.id, b.id, c.id})
