from odoo.tests.common import tagged

from .common import AttachmentManagementCase


@tagged('post_install', '-at_install')
class TestPreviewActions(AttachmentManagementCase):

    def _attach(self, name, mimetype):
        return self.env['ir.attachment'].sudo().create({
            'name': name, 'mimetype': mimetype, 'raw': b'x',
            'res_model': 'res.partner', 'res_id': self.env.user.partner_id.id,
        })

    def test_preview_opens_client_action(self):
        """Requirements: FR-AM-0006. Preview returns the viewer client action for the file."""
        att = self._attach('p.pdf', 'application/pdf')
        action = att.action_preview()
        self.assertEqual(action['tag'], 'attachment_management.preview')
        self.assertEqual(action['params']['attachment_id'], att.id)

    def test_download_returns_download_url(self):
        """Requirements: FR-AM-0007. Download links to the file with the download flag."""
        att = self._attach('s.xlsx', 'application/vnd.ms-excel')
        action = att.action_download()
        self.assertEqual(action['type'], 'ir.actions.act_url')
        self.assertIn('/web/content/%d?download=true' % att.id, action['url'])
