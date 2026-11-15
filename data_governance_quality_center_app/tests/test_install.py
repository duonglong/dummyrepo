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
        self.assertIn('data.quality.dashboard', self.env)
        for model in ('data.quality.profile', 'data.quality.profile.line', 'data.quality.score.snapshot'):
            self.assertIn(model, self.env, model)
            self.assertTrue(
                self.env['ir.access'].sudo().search_count([('model_id.model', '=', model)]), model)
        data = Manifest.for_addon('data_governance_quality_center_app')['data']
        self.assertIn('views/profile_views.xml', data)
        self.assertIn('views/dashboard_views.xml', data)

    def test_bug_b_0200_profile_loads_beside_the_epic_settings_report_and_layout(self):
        """Regression test for B-0200: after merging the current epic, the profile and the epic's
        settings, cleanup, report and widget layout parts all load and keep their access rows.

        Reaches only the model registry, the access rows and the manifest list; it does not open a screen.

        Requirements: NFR-DGQ-0009
        """
        for model in ('data.quality.profile', 'data.quality.profile.line',
                      'data.quality.report.wizard', 'data.quality.dashboard.layout'):
            self.assertIn(model, self.env, model)
            self.assertTrue(
                self.env['ir.access'].sudo().search_count([('model_id.model', '=', model)]), model)
        self.assertIn('data.quality.retention', self.env)
        data = Manifest.for_addon('data_governance_quality_center_app')['data']
        for path in ('views/profile_views.xml', 'views/report_views.xml',
                     'views/res_config_settings_views.xml', 'data/cron_cleanup.xml'):
            self.assertIn(path, data)
