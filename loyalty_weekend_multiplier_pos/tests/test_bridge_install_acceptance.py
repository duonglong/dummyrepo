from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install')
class TestPosBridgeInstallAcceptance(TransactionCase):
    """Acceptance tests of T-012: the Point of Sale bridge module.

    Requirements: FR-LAP-008, FR-LAP-015, FR-LAP-017
    """

    def _module(self, name):
        return self.env['ir.module.module'].sudo().search([('name', '=', name)])

    def test_manifest_depends_and_auto_install(self):
        """Requirements: FR-LAP-017 - SC-LAP-017-1: the bridge depends on core and pos_loyalty, auto-installs."""
        module = self._module('loyalty_weekend_multiplier_pos')
        self.assertTrue(module, 'bridge module is missing')
        self.assertEqual(set(module.dependencies_id.mapped('name')),
                         {'loyalty_weekend_multiplier', 'pos_loyalty'})
        self.assertTrue(module.auto_install)

    def test_manifest_metadata(self):
        """Requirements: FR-LAP-017 - the bridge is LGPL-3 and version 20.0.2.0.0."""
        module = self._module('loyalty_weekend_multiplier_pos')
        self.assertTrue(module, 'bridge module is missing')
        self.assertEqual(module.license, 'LGPL-3')
        self.assertTrue(module.latest_version.startswith('20.0.2.0.0')
                        or module.installed_version == '20.0.2.0.0')

    def test_bridge_installed_with_core_and_pos_loyalty(self):
        """Requirements: FR-LAP-017 - SC-LAP-017-2: the bridge is installed when core and pos_loyalty are."""
        self.assertEqual(self._module('loyalty_weekend_multiplier').state, 'installed')
        self.assertEqual(self._module('pos_loyalty').state, 'installed')
        self.assertEqual(self._module('loyalty_weekend_multiplier_pos').state, 'installed')

    def test_sales_not_installed_and_not_required(self):
        """Requirements: FR-LAP-015 - SC-LAP-015-1: POS works without Sales; the bridge does not pull it."""
        module = self._module('loyalty_weekend_multiplier_pos')
        self.assertTrue(module, 'bridge module is missing')
        deps = module.dependencies_id.mapped('name')
        self.assertNotIn('sale', deps)
        self.assertNotIn('sale_loyalty', deps)
        self.assertNotEqual(self._module('sale').state, 'installed')

    def test_multiplier_in_pos_loaded_data(self):
        """Requirements: FR-LAP-008 - SC-LAP-008-1: the POS loads weekend_multiplier to show 200.00 points."""
        self.assertIn('weekend_multiplier',
                      self.env['loyalty.program']._load_pos_data_fields(False))
