from odoo.tests.common import tagged

from .common import WeekendPointsCommon


@tagged('post_install', '-at_install')
class TestInstall(WeekendPointsCommon):
    """Requirements: NFR-COMP-001"""

    def test_installed_without_enterprise_dependency(self):
        """Module is installed and its dependency closure holds no Enterprise module."""
        module = self.env['ir.module.module'].search(
            [('name', '=', 'loyalty_weekend_double_points')])
        self.assertEqual(module.state, 'installed')
        seen, todo = set(), module
        while todo:
            seen |= set(todo.ids)
            todo = todo.dependencies_id.mapped('depend_id').filtered(
                lambda m: m.id not in seen)
        closure = self.env['ir.module.module'].browse(seen)
        self.assertFalse(closure.filtered(lambda m: m.license and m.license.startswith('OEEL')))
