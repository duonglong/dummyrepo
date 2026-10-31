import ast
from pathlib import Path

from odoo.tests import tagged
from odoo.tests.common import TransactionCase

MANIFEST = Path(__file__).resolve().parent.parent / '__manifest__.py'


@tagged('post_install', '-at_install')
class TestSaleBridgeManifest(TransactionCase):
    """The Sales bridge installs itself and needs nothing from Point of Sale.

    Requirements: FR-LAP-014, FR-LAP-016
    """

    def test_manifest_auto_install_without_pos(self):
        """Requirements: FR-LAP-016 - auto_install on core plus sale_loyalty, no POS dependency."""
        manifest = ast.literal_eval(MANIFEST.read_text())
        self.assertIs(manifest['auto_install'], True)
        self.assertEqual(sorted(manifest['depends']), ['loyalty_weekend_multiplier', 'sale_loyalty'])

    def test_sale_order_override_loaded(self):
        """Requirements: FR-LAP-014 - the sale order override comes from the bridge."""
        method = type(self.env['sale.order'])._program_check_compute_points
        self.assertIn('loyalty_weekend_multiplier_sale', method.__module__)
