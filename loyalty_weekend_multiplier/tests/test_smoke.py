import importlib

from odoo.modules.module import get_manifest

from .common import LoyaltyWeekendCommon

MODULE = 'loyalty_weekend_multiplier'


class TestSmoke(LoyaltyWeekendCommon):
    """Smoke tests of the module skeleton (T-001)."""

    def test_module_installed(self):
        """Requirements: NFR-COMP-001 - the module installs on Community."""
        module = self.env['ir.module.module'].search([('name', '=', MODULE)])
        self.assertEqual(module.state, 'installed')

    def test_depends_on_community_only(self):
        """Requirements: NFR-COMP-001 - no dependency is an Enterprise module."""
        closure = set()
        todo = list(get_manifest(MODULE)['depends'])
        while todo:
            name = todo.pop()
            if name in closure:
                continue
            closure.add(name)
            manifest = get_manifest(name)
            self.assertTrue(manifest, f"Dependency {name} is not available")
            self.assertNotEqual(manifest.get('license'), 'OEEL-1', f"{name} is Enterprise")
            todo.extend(manifest.get('depends', []))
        self.assertIn('loyalty', closure)

    def test_extends_native_loyalty(self):
        """Requirements: NFR-MNT-001 - the module extends the native loyalty app."""
        importlib.import_module(f'odoo.addons.{MODULE}.models')
        program = self._create_loyalty_program()
        self.assertEqual(program.program_type, 'loyalty')
        self.assertEqual(program._name, 'loyalty.program')
