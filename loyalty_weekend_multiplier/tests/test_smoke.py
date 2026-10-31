import os

from odoo.modules.module import get_manifest

from .common import WeekendMultiplierCase

MODULE = 'loyalty_weekend_multiplier'


class TestSmoke(WeekendMultiplierCase):
    """Smoke tests of the module skeleton (T-001)."""

    def test_module_installed(self):
        """Requirements: NFR-COMP-001 - the module installs and loyalty.program exists."""
        module = self.env['ir.module.module'].search([('name', '=', MODULE)])
        self.assertEqual(module.state, 'installed')
        self.assertIn('loyalty.program', self.env)

    def test_base_fixtures(self):
        """Requirements: NFR-MNT-001 - the shared fixtures build on native loyalty records."""
        self.assertEqual(self.program.program_type, 'loyalty')
        self.assertEqual(len(self.program.rule_ids), 1)
        self.assertEqual(self.program.rule_ids.reward_point_mode, 'money')
        self.assertEqual(self.program.rule_ids.reward_point_amount, 1)
        self.assertTrue(self.partner.exists())
        self.assertEqual(self.product.lst_price, 100.0)
        users = self.user_brussels | self.user_new_york | self.user_no_tz
        self.assertEqual(len(users), 3)
        self.assertEqual(self.user_brussels.tz, 'Europe/Brussels')
        self.assertEqual(self.user_new_york.tz, 'America/New_York')
        self.assertFalse(self.user_no_tz.tz)

    def test_no_enterprise_dependency(self):
        """Requirements: NFR-COMP-001 - no module of the core or bridge closures is Enterprise."""
        enterprise = os.environ.get('SDD_ENTERPRISE_SRC')
        enterprise = enterprise and os.path.realpath(enterprise)
        names = [MODULE] + [
            name for name in self.env['ir.module.module'].search(
                [('name', '=like', MODULE + '\\_%')]).mapped('name')
        ]
        closure = set()
        todo = list(names)
        while todo:
            name = todo.pop()
            if name in closure:
                continue
            closure.add(name)
            manifest = get_manifest(name)
            self.assertTrue(manifest, f"Dependency {name} is not available")
            module_path = os.path.realpath(os.path.join(manifest['addons_path'], name))
            self.assertFalse(
                enterprise and module_path.startswith(enterprise + os.sep),
                f"{name} comes from the Enterprise source",
            )
            self.assertNotEqual(manifest.get('license'), 'OEEL-1', f"{name} is Enterprise")
            todo.extend(manifest.get('depends', []))
