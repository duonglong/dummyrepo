"""Own tests of B-0179: a public file of a record the user cannot open is not offered as a document of it.

Odoo 20 lets every internal user read an attachment flagged public, whatever its record.
Requirements: BR-AM-0001, BR-AM-0002, FR-AM-0002, FR-AM-0003, FR-AM-0006, FR-AM-0008, FR-AM-0010, FR-AM-0016.
"""
import json

from odoo.exceptions import AccessError
from odoo.tests.common import TransactionCase, new_test_user, tagged

DUPLICATE_EVENT = 'attachment_management/duplicate'
SECRET_BYTES = b'am-public-secret'


@tagged('post_install', '-at_install')
class TestPublicAttachmentOfHiddenRecord(TransactionCase):
    """Requirements: BR-AM-0001, FR-AM-0002, FR-AM-0016, FR-AM-0010. One row per place the module offers an attachment."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = new_test_user(
            cls.env, login='am_public_user', groups='base.group_user,base.group_partner_manager')
        cls.other = new_test_user(cls.env, login='am_public_other', groups='base.group_user')
        cls.admin = new_test_user(cls.env, login='am_public_admin', groups='base.group_system')
        cls.parameter = cls.env['ir.config_parameter'].create({'key': 'am.public.secret', 'value': 'x'})
        cls.partner = cls.env['res.partner'].create({'name': 'AmPublicPartner'})
        Attachment = cls.env['ir.attachment']
        cls.hidden_public = Attachment.create({
            'name': 'secret.pdf', 'raw': SECRET_BYTES, 'public': True,
            'res_model': 'ir.config_parameter', 'res_id': cls.parameter.id})
        cls.open_public = Attachment.create({
            'name': 'open.pdf', 'raw': b'am-public-open', 'public': True,
            'res_model': 'res.partner', 'res_id': cls.partner.id})
        cls.others_loose = Attachment.with_user(cls.other).create({
            'name': 'loose.pdf', 'raw': b'am-public-loose', 'public': True})
        cls.own_loose = Attachment.with_user(cls.user).create({
            'name': 'mine.pdf', 'raw': b'am-public-mine', 'public': True})

    def _central(self, user=None):
        user = user or self.user
        return self.env['ir.attachment'].with_user(user).with_context(am_central_list=True)

    def test_central_action_marks_its_list(self):
        """Requirements: FR-AM-0002. The menu action passes the key that makes the search check the record."""
        action = self.env.ref('attachment_management.action_attachment_central')
        self.assertIn('am_central_list', action.context)

    def test_central_list_hides_public_file_of_hidden_record(self):
        """Requirements: BR-AM-0001, FR-AM-0002. The list and its count leave out the hidden record's public file."""
        found = self._central().search([('type', '=', 'binary'), ('url', '=', False)])
        self.assertNotIn(self.hidden_public, found)
        self.assertIn(self.open_public, found)
        self.assertEqual(
            self._central().search_count([('id', 'in', (self.hidden_public | self.open_public).ids)]), 1)

    def test_central_list_hides_public_file_without_record_of_someone_else(self):
        """Requirements: BR-AM-0002, FR-AM-0003. Only the uploader and administrators find a file with no record."""
        found = self._central().search([('type', '=', 'binary'), ('url', '=', False)])
        self.assertNotIn(self.others_loose, found)
        self.assertIn(self.own_loose, found)
        self.assertIn(self.others_loose, self._central(self.admin).search([('type', '=', 'binary')]))

    def test_record_label_of_hidden_record_is_not_read(self):
        """Requirements: BR-AM-0001. The stored name of the hidden record is not handed to the user."""
        as_user = self.hidden_public.with_user(self.user)
        self.assertFalse(as_user.read(['record_label'])[0]['record_label'])
        self.assertFalse(as_user.web_read({'record_label': {}})[0]['record_label'])
        self.assertEqual(self.open_public.with_user(self.user).read(['record_label'])[0]['record_label'], 'AmPublicPartner')

    def test_group_by_record_does_not_name_hidden_record(self):
        """Requirements: BR-AM-0001, FR-AM-0004. Grouping the central list by Record shows no hidden record name."""
        groups = self._central().formatted_read_group([], groupby=['record_label'], aggregates=['__count'])
        self.assertNotIn(self.hidden_public.record_label, [group['record_label'] for group in groups])

    def test_duplicate_warning_does_not_name_public_copy_of_hidden_record(self):
        """Requirements: FR-AM-0016. The warning names no copy whose record the user cannot open."""
        self.env['bus.bus'].sudo().search([]).unlink()
        self.env.cr.precommit.data.get('bus.bus.values', []).clear()
        self.env['ir.attachment'].with_user(self.user).with_context(am_name_from_file=True).create({
            'name': 'copy.pdf', 'raw': SECRET_BYTES, 'res_model': 'res.partner', 'res_id': self.partner.id})
        self.env.flush_all()
        self.env.cr.precommit.run()
        payloads = [
            json.loads(notif.message)['payload'] for notif in self.env['bus.bus'].sudo().search([])
            if json.loads(notif.message).get('type') == DUPLICATE_EVENT]
        self.assertEqual(len(payloads), 1)
        self.assertNotIn('secret.pdf', json.dumps(payloads))
        self.assertNotIn('am.public.secret', json.dumps(payloads))
        self.assertEqual(payloads[0]['copies'], [])
        self.assertIn('already stored', payloads[0]['message'])

    def test_zip_request_refuses_public_file_of_hidden_record(self):
        """Requirements: FR-AM-0010. One file whose record the user cannot open refuses the whole zip."""
        Zip = self.env['attachment.zip.download'].with_user(self.user)
        with self.assertRaises(AccessError):
            Zip._request_from_attachments((self.open_public | self.hidden_public).with_user(self.user))
        self.assertFalse(self.env['attachment.zip.download'].sudo().search([('user_id', '=', self.user.id)]))

    def test_zip_build_refuses_public_file_of_hidden_record(self):
        """Requirements: FR-AM-0010. The build re-checks at download time, when the record may have been hidden since."""
        download = self.env['attachment.zip.download'].sudo().create({
            'user_id': self.user.id, 'zip_name': 'a.zip', 'state': 'ready',
            'attachment_ids': [(6, 0, (self.open_public | self.hidden_public).ids)]})
        with self.assertRaises(AccessError):
            download.with_user(self.user)._build()

    def test_preview_and_download_refuse_public_file_of_hidden_record(self):
        """Requirements: BR-AM-0001, FR-AM-0006. The form actions do not open such a file."""
        as_user = self.hidden_public.with_user(self.user)
        with self.assertRaises(AccessError):
            as_user.action_preview()
        with self.assertRaises(AccessError):
            as_user.action_download()
        self.assertTrue(self.open_public.with_user(self.user).action_preview())
