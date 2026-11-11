"""Own tests of T-006: the duplicate warning, with real file content (``raw``).

Odoo 20 ignores ``datas`` on create, so these tests give the content as ``raw``.
Requirements: BR-AM-0003, FR-AM-0014, FR-AM-0015, FR-AM-0016.
"""
import json

from odoo.tests.common import HttpCase, TransactionCase, new_test_user, tagged

DUPLICATE_EVENT = 'attachment_management/duplicate'


@tagged('post_install', '-at_install')
class TestDuplicate(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        groups = 'base.group_user,base.group_partner_manager'
        cls.user = new_test_user(cls.env, login='am_dup_own', groups=groups)
        Partner = cls.env['res.partner']
        cls.contact = Partner.create({'name': 'DupOwnContact'})
        cls.target = Partner.create({'name': 'DupOwnTarget'})
        cls.hidden = Partner.create({'name': 'DupOwnHidden'})
        cls.env['ir.access'].create({
            'name': 'am_dup_own_hide',
            'model_id': cls.env['ir.model']._get('res.partner').id,
            'operation': 'r',
            'domain': [('name', '!=', 'DupOwnHidden')],
        })
        cls.env.transaction.invalidate_access_cache()

    def _create(self, record, name, raw=b'am-dup-own', user=None, **context):
        env = self.env(user=user) if user else self.env(su=True)
        return env['ir.attachment'].with_context(**context).create({
            'name': name, 'raw': raw, 'res_model': record._name, 'res_id': record.id})

    def _warnings(self):
        self.env.flush_all()
        self.env.cr.precommit.run()
        events = []
        for notif in self.env['bus.bus'].sudo().search([]):
            message = json.loads(notif.message)
            if message.get('type') == DUPLICATE_EVENT:
                events.append(message['payload'])
        return events

    def _reset_bus(self):
        self.env.cr.precommit.data.get('bus.bus.values', []).clear()
        self.env['bus.bus'].sudo().search([]).unlink()

    def _upload(self, name='copy.pdf', raw=b'am-dup-own', **context):
        self._reset_bus()
        context.setdefault('am_name_from_file', True)
        attachment = self._create(self.target, name, raw, user=self.user, **context)
        return attachment, self._warnings()

    def test_same_content_other_name_warns_and_names_openable_copy(self):
        """Requirements: FR-AM-0014, FR-AM-0016, BR-AM-0003. A renamed copy warns and names the openable copy only."""
        self._create(self.contact, 'contract.pdf')
        self._create(self.hidden, 'secret.pdf')
        _attachment, warnings = self._upload()
        self.assertEqual(len(warnings), 1)
        self.assertIn('DupOwnContact', warnings[0]['message'])
        self.assertNotIn('DupOwnHidden', json.dumps(warnings))
        self.assertNotIn('secret.pdf', json.dumps(warnings))
        self.assertEqual([copy['res_id'] for copy in warnings[0]['copies']], [self.contact.id])

    def test_only_hidden_copy_says_only_already_stored(self):
        """Requirements: FR-AM-0016. With no openable copy the warning names nothing."""
        self._create(self.hidden, 'secret.pdf')
        _attachment, warnings = self._upload()
        self.assertEqual(len(warnings), 1)
        self.assertIn('already stored', warnings[0]['message'])
        self.assertEqual(warnings[0]['copies'], [])
        self.assertNotIn('DupOwnHidden', json.dumps(warnings))

    def test_same_name_other_content_does_not_warn(self):
        """Requirements: FR-AM-0014, BR-AM-0003. Same name, different content: no warning."""
        self._create(self.contact, 'contract.pdf')
        _attachment, warnings = self._upload('contract.pdf', b'am-dup-different')
        self.assertEqual(warnings, [])

    def test_upload_is_kept(self):
        """Requirements: FR-AM-0015. The warned upload stays attached to its record."""
        self._create(self.contact, 'contract.pdf')
        attachment, warnings = self._upload()
        self.assertTrue(warnings)
        self.assertEqual((attachment.res_model, attachment.res_id), ('res.partner', self.target.id))

    def test_five_copies_named_sixth_not(self):
        """Requirements: FR-AM-0016. At most five copies are named."""
        records = self.env['res.partner'].create([{'name': 'DupOwnHolder%d' % i} for i in range(6)])
        for record in records:
            self._create(record, 'c.pdf')
        _attachment, warnings = self._upload()
        self.assertEqual(len(warnings[0]['copies']), 5)

    def test_skip_context_and_field_files_send_no_warning(self):
        """Requirements: FR-AM-0014. The skip key sends nothing; a picture-field file is not a copy."""
        self.env['ir.attachment'].sudo().create({
            'name': 'image_1920', 'raw': b'am-dup-own', 'res_model': 'res.partner',
            'res_id': self.contact.id, 'res_field': 'image_1920'})
        _attachment, warnings = self._upload()
        self.assertEqual(warnings, [])
        self._create(self.contact, 'contract.pdf')
        _attachment, warnings = self._upload(am_skip_duplicate_check=True)
        self.assertEqual(warnings, [])

    def test_copy_sends_no_warning(self):
        """Requirements: FR-AM-0014. Copying an attachment is not an upload: no warning."""
        original = self._create(self.contact, 'contract.pdf')
        self._reset_bus()
        original.with_user(self.user).copy({'res_id': self.target.id})
        self.assertEqual(self._warnings(), [])

    def test_file_nobody_uploaded_sends_no_warning(self):
        """Requirements: FR-AM-0014. A create without the upload marker (calendar, expenses) sends no warning."""
        self._create(self.contact, 'contract.pdf')
        self._reset_bus()
        self._create(self.target, 'copy.pdf', user=self.user)
        self.assertEqual(self._warnings(), [])

    def _partner_report_template(self):
        view = self.env['ir.ui.view'].create({
            'name': 'am_dup_report', 'type': 'qweb', 'key': 'attachment_management.am_dup_report',
            'arch': '<t t-name="attachment_management.am_dup_report">'
                    '<t t-foreach="docs" t-as="doc"><p>AM report <t t-out="doc.name"/></p></t></t>'})
        report = self.env['ir.actions.report'].create({
            'name': 'AM duplicate report', 'model': 'res.partner', 'report_type': 'qweb-pdf',
            'report_name': view.key})
        return self.env['mail.template'].create({
            'name': 'AM duplicate template', 'model_id': self.env['ir.model']._get_id('res.partner'),
            'report_template_ids': [(6, 0, report.ids)]})

    def test_b_0176_template_report_rendered_twice_sends_no_warning(self):
        """Requirements: FR-AM-0014. B-0176: a mail template's printed report (mail composer) is not an upload.

        Opening the composer twice with the same template renders the same report
        twice; nobody uploaded it, so no warning.
        """
        template = self._partner_report_template()
        Composer = self.env['mail.compose.message'].with_user(self.user)
        values = {'composition_mode': 'comment', 'model': 'res.partner', 'res_ids': [self.contact.id],
                  'template_id': template.id}
        first = Composer.create(values)
        self.assertTrue(first.attachment_ids, "The template rendered no report")
        self._reset_bus()
        Composer.create(values)
        self.assertEqual(self._warnings(), [])

    def test_b_0176_template_attachment_copies_send_no_warning(self):
        """Requirements: FR-AM-0014. B-0176: copies of a template's files (calendar invitations, expense receipts).

        Calendar and expenses copy a stored file with copy_data then create, not
        with copy(); those apps are not installed here, so the test does the same.
        """
        original = self._create(self.contact, 'contract.pdf')
        self._reset_bus()
        values = original.copy_data({'res_model': 'mail.compose.message', 'res_id': 0, 'raw': original.raw})
        self.env['ir.attachment'].with_user(self.user).create(values)
        self.assertEqual(self._warnings(), [])

    def test_b_0176_posted_message_attachments_send_no_warning(self):
        """Requirements: FR-AM-0014. B-0176: files code or an incoming email posts on a record are not uploads."""
        self._create(self.contact, 'contract.pdf')
        self._reset_bus()
        self.target.with_user(self.user).message_post(body='AM', attachments=[('copy.pdf', b'am-dup-own')])
        self.assertEqual(self._warnings(), [])


@tagged('post_install', '-at_install')
class TestDuplicateUploadRoutes(HttpCase):
    """Requirements: FR-AM-0014. B-0176: a person's upload through either upload screen still warns.

    The routes are called as the browser calls them; the notification itself is
    shown by the browser and is not reached here: the test reads the bus event.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = new_test_user(cls.env, login='am_dup_route', groups='base.group_user,base.group_partner_manager')
        cls.contact = cls.env['res.partner'].create({'name': 'DupRouteContact'})
        cls.target = cls.env['res.partner'].create({'name': 'DupRouteTarget'})
        cls.env['ir.attachment'].create({
            'name': 'contract.pdf', 'raw': b'am-dup-route', 'res_model': 'res.partner', 'res_id': cls.contact.id})

    def _warnings(self):
        events = [json.loads(notif.message) for notif in self.env['bus.bus'].sudo().search([])]
        return [event['payload'] for event in events if event.get('type') == DUPLICATE_EVENT]

    def _post_upload(self, url, data, file_field='ufile'):
        self.authenticate(self.user.login, self.user.login)
        self.env['bus.bus'].sudo().search([]).unlink()
        response = self.url_open(
            url, data=dict(data, csrf_token=self.csrf_token()),
            files={file_field: ('copy.pdf', b'am-dup-route', 'application/pdf')})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(self.env['ir.attachment'].sudo().search_count(
            [('res_model', '=', 'res.partner'), ('res_id', '=', self.target.id)]))

    def test_b_0176_chatter_upload_warns(self):
        """Requirements: FR-AM-0014. The chatter upload of a stored file warns."""
        self._post_upload('/mail/attachment/upload', {'thread_id': str(self.target.id), 'thread_model': 'res.partner'})
        self.assertEqual(len(self._warnings()), 1)

    def test_b_0176_file_field_upload_warns(self):
        """Requirements: FR-AM-0014. The file widget upload (many2many_binary) of a stored file warns."""
        self._post_upload('/web/binary/upload_attachment', {'model': 'res.partner', 'id': str(self.target.id)})
        self.assertEqual(len(self._warnings()), 1)
