from odoo.exceptions import AccessError
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestAccess(TransactionCase):
    """Requirements: FR-OPA-0033 (SC-OPA-0033-1, SC-OPA-0033-2 for checks)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Users = cls.env['res.users']
        cls.admin = Users.create({
            'name': 'Admin T2', 'login': 'perf_t2_admin',
            'group_ids': [(6, 0, [cls.env.ref('base.group_system').id])]})
        cls.plain = Users.create({
            'name': 'Plain T2', 'login': 'perf_t2_plain',
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id])]})

    def test_plain_user_refused_on_every_operation(self):
        """SC-OPA-0033-1: an internal user cannot read, write, create or delete."""
        check = self.env['perf.audit.check'].search([], limit=1)
        Check = self.env['perf.audit.check'].with_user(self.plain)
        with self.assertRaises(AccessError):
            Check.search([])
        with self.assertRaises(AccessError):
            check.with_user(self.plain).write({'active_check': False})
        with self.assertRaises(AccessError):
            Check.create({'code': 'ZZ', 'name': 'x', 'category': 'odoo'})
        with self.assertRaises(AccessError):
            check.with_user(self.plain).unlink()

    def test_admin_reads_checks(self):
        """SC-OPA-0033-2: a system administrator sees the checks."""
        self.assertEqual(self.env['perf.audit.check'].with_user(self.admin).search_count([]), 15)

    def test_access_rows_only_for_system_group(self):
        """FR-OPA-0033: no access row of the module names another group."""
        accesses = self.env['ir.access'].search([('model_id.model', 'like', 'perf.audit.%')])
        self.assertTrue(accesses)
        self.assertEqual(accesses.group_id, self.env.ref('base.group_system'))

    def test_menu_visible_only_to_administrators(self):
        """FR-OPA-0033: the top-level menu is visible to an administrator, not to a plain user."""
        menu = self.env.ref('perf_audit.menu_perf_audit_root')
        Menu = self.env['ir.ui.menu']
        self.assertIn(menu.id, Menu.with_user(self.admin)._visible_menu_ids())
        self.assertNotIn(menu.id, Menu.with_user(self.plain)._visible_menu_ids())
