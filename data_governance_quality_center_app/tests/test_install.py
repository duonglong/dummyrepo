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
            depends, {'mail', 'mail_tracking', 'product', 'base_import', 'phone_validation'})
        self.assertNotIn('queue_job', depends)
        self.assertEqual(manifest['license'], 'LGPL-3')

    def test_bug_b_0200_profile_and_dashboard_models_both_load(self):
        """Regression test for B-0200: the profile and the dashboard parts both load after the epic merge.

        Reaches only the model registry, the access rows and the manifest list; it does not open a screen.

        Requirements: NFR-DGQ-0009
        """
        for model in ('data.quality.profile', 'data.quality.profile.line',
                      'data.quality.score.snapshot', 'data.quality.dashboard'):
            self.assertIn(model, self.env, model)
            self.assertTrue(
                self.env['ir.access'].sudo().search_count([('model_id.model', '=', model)]), model)
        data = Manifest.for_addon('data_governance_quality_center_app')['data']
        self.assertIn('views/profile_views.xml', data)
        self.assertIn('views/dashboard_views.xml', data)
