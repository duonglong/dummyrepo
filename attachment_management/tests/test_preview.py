from odoo.exceptions import AccessError, UserError
from odoo.tests.common import HttpCase, tagged

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


@tagged('post_install', '-at_install')
class TestOpenRecordAction(AttachmentManagementCase):
    """Requirements: UX-AM-001, BR-AM-0001. B-0183: the file leads to its record."""

    def _attach(self, **values):
        return self.env['ir.attachment'].sudo().create(dict(
            {'name': 'o.pdf', 'mimetype': 'application/pdf', 'raw': b'x'}, **values))

    def test_open_record_returns_the_record_form(self):
        att = self._attach(res_model='res.partner', res_id=self.env.user.partner_id.id)
        action = att.with_user(self.am_user).action_open_record()
        self.assertEqual(action['type'], 'ir.actions.act_window')
        self.assertEqual((action['res_model'], action['res_id']), ('res.partner', att.res_id))
        self.assertEqual(action['views'], [(False, 'form')])

    def test_open_record_refused_when_the_record_is_not_open_to_the_user(self):
        parameter = self.env['ir.config_parameter'].create({'key': 'am.open.record', 'value': 'x'})
        att = self._attach(res_model='ir.config_parameter', res_id=parameter.id)
        with self.assertRaises(AccessError):
            att.with_user(self.am_user).action_open_record()

    def test_open_record_of_a_file_with_no_record_says_so(self):
        att = self._attach()
        with self.assertRaises(UserError):
            att.with_user(self.am_admin).action_open_record()


@tagged('post_install', '-at_install')
class TestOpenRecordScreen(HttpCase):
    """Requirements: UX-AM-001. B-0183: from the Attachments list and form a person jumps to the record.

    The tours click the row button and the form header button as the user does and read the
    record form that opens. They do not reach other kinds of record than a contact.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        partner = cls.env['res.partner'].create({'name': 'AM Link Partner'})
        cls.env['ir.attachment'].create({
            'name': 'link.pdf', 'mimetype': 'application/pdf', 'raw': b'x',
            'res_model': 'res.partner', 'res_id': partner.id,
        })

    def test_b_0183_row_button_opens_the_record(self):
        self.start_tour('/odoo/action-attachment_management.action_attachment_central',
                        'am_open_record_row_tour', login='admin')

    def test_b_0183_form_button_opens_the_record(self):
        self.start_tour('/odoo/action-attachment_management.action_attachment_central',
                        'am_open_record_form_tour', login='admin')
