from odoo.modules.module import Manifest

from .common import SfdCommon


class TestSmoke(SfdCommon):
    """Smoke test of the module skeleton.

    Requirements: NFR-SEC-0002 (the skeleton adds no model, field or record
    to another app: it only depends on web).
    """

    def test_module_is_installed(self):
        module = self.env['ir.module.module'].search([('name', '=', 'smart_field_dependency_viewer')])
        self.assertEqual(module.state, 'installed')

    def test_skeleton_depends_on_web_only(self):
        manifest = Manifest.for_addon('smart_field_dependency_viewer')
        self.assertEqual(manifest['depends'], ['web'])

    def test_base_class_users(self):
        self.assertTrue(self.sfd_admin.has_group('base.group_system'))
        self.assertFalse(self.sfd_user.has_group('base.group_system'))
