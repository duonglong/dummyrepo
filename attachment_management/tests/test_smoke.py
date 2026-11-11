from odoo.tests.common import tagged

from .common import AttachmentManagementCase


@tagged('post_install', '-at_install')
class TestModuleSmoke(AttachmentManagementCase):
    """Requirements: FR-AM-0001 (the module installs on top of mail)."""

    def test_module_is_installed_with_mail(self):
        module = self.env['ir.module.module'].search([('name', '=', 'attachment_management')])
        self.assertEqual(module.state, 'installed')
        self.assertIn('mail', module.dependencies_id.mapped('name'))
