from odoo.modules.module import Manifest
from odoo.tests import tagged

from .common import DGQTestCase


@tagged('post_install', '-at_install')
class TestInstall(DGQTestCase):
    """Requirements: NFR-DGQ-0008, NFR-DGQ-0009"""

    def test_module_installed(self):
        module = self.env['ir.module.module'].search(
            [('name', '=', 'data_governance_quality_center_app')])
        self.assertEqual(module.state, 'installed')

    def test_community_dependencies_only(self):
        """No Enterprise module and no queue add-on in the dependencies."""
        manifest = Manifest.for_addon('data_governance_quality_center_app')
        depends = set(manifest['depends'])
        self.assertEqual(
            depends, {'mail', 'product', 'base_import', 'phone_validation'})
        self.assertNotIn('queue_job', depends)
        self.assertEqual(manifest['license'], 'LGPL-3')
