"""Acceptance tests of T-005: naming patterns per kind of record applied at upload.

Requirements: FR-AM-0011, FR-AM-0012, FR-AM-0013, FR-AM-0017.

Sales orders and project tasks are not installed with this module, so the
scenarios run on Contact (res.partner); the contact's name and reference are
both set to the same text so any reading of {record_ref} gives it. A person's
upload is the native chatter route /mail/attachment/upload. A typed name has
no field in that route, so SC-AM-0017-1 creates the attachment through the
ORM with a name of its own. The rule form is driven with Form (fields
model_id and pattern, then save).
"""
from odoo.exceptions import AccessError, ValidationError
from odoo.fields import Date
from odoo.tests import Form
from odoo.tests.common import HttpCase, new_test_user, tagged


@tagged('post_install', '-at_install')
class TestNamingAcceptance(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.admin = new_test_user(cls.env, login='am_nm_admin', groups='base.group_system')
        cls.user = new_test_user(cls.env, login='am_nm_user', groups='base.group_user')
        cls.partner_model = cls.env['ir.model']._get('res.partner')
        cls.order = cls.env['res.partner'].create({'name': 'S00042', 'ref': 'S00042'})
        cls.trading = cls.env['res.partner'].create({'name': 'A/B Trading', 'ref': 'ABT'})

    def _make_rule(self, pattern, model=None, active=True):
        form = Form(self.env['attachment.naming.rule'].with_user(self.admin))
        form.model_id = model or self.partner_model
        form.pattern = pattern
        if not active:
            form.active = False
        return form.save()

    def _upload(self, record, filename, content=b'am-naming'):
        self.authenticate(self.user.login, self.user.login)
        response = self.url_open(
            '/mail/attachment/upload',
            data={'csrf_token': self.csrf_token(), 'thread_id': str(record.id),
                  'thread_model': record._name},
            files={'ufile': (filename, content, 'application/octet-stream')})
        self.assertEqual(response.status_code, 200)
        attachment = self.env['ir.attachment'].sudo().search(
            [('res_model', '=', record._name), ('res_id', '=', record.id)],
            order='id desc', limit=1)
        self.assertTrue(attachment, "The upload created no attachment")
        return attachment

    # -- FR-AM-0011 ------------------------------------------------------
    def test_sc_0011_1_second_active_pattern_refused(self):
        """Requirements: FR-AM-0011. SC-AM-0011-1: a second active pattern for the
        same kind of record is refused with a message about the existing pattern."""
        self._make_rule('{record_ref}_{date}')
        with self.assertRaises(ValidationError) as caught:
            self._make_rule('{record_name}')
        self.assertIn('pattern', str(caught.exception).lower())

    def test_fr_0011_inactive_second_pattern_allowed(self):
        """Requirements: FR-AM-0011. At most one ACTIVE pattern: an inactive second one saves."""
        self._make_rule('{record_ref}_{date}')
        rule = self._make_rule('{record_name}', active=False)
        self.assertFalse(rule.active)

    def test_fr_0011_unknown_placeholder_refused(self):
        """Requirements: FR-AM-0011. A pattern with a placeholder outside the list is refused."""
        with self.assertRaises(ValidationError):
            self._make_rule('{record_ref}_{no_such_placeholder}')

    def test_sc_0011_2_non_admin_cannot_open_patterns(self):
        """Requirements: FR-AM-0011. SC-AM-0011-2: an internal user who is not an
        administrator is refused access to the naming patterns."""
        self._make_rule('{record_name}')
        with self.assertRaises(AccessError):
            self.env['attachment.naming.rule'].with_user(self.user).search([])

    # -- FR-AM-0012 ------------------------------------------------------
    def test_sc_0012_1_pattern_names_the_upload_and_keeps_extension(self):
        """Requirements: FR-AM-0012. SC-AM-0012-1: {record_ref}_{date} on scan.pdf gives
        S00042_<upload date>.pdf."""
        self._make_rule('{record_ref}_{date}')
        attachment = self._upload(self.order, 'scan.pdf')
        self.assertEqual(attachment.name, 'S00042_%s.pdf' % Date.context_today(attachment))

    def test_sc_0012_2_forbidden_characters_become_underscore(self):
        """Requirements: FR-AM-0012. SC-AM-0012-2: {record_name} of "A/B Trading" on
        logo.png gives "A_B Trading.png"."""
        self._make_rule('{record_name}')
        attachment = self._upload(self.trading, 'logo.png')
        self.assertEqual(attachment.name, 'A_B Trading.png')

    # -- FR-AM-0013 ------------------------------------------------------
    def test_sc_0013_1_no_pattern_keeps_uploaded_name(self):
        """Requirements: FR-AM-0013. SC-AM-0013-1: a kind of record with no pattern keeps notes.docx."""
        self._make_rule('{record_name}', model=self.env['ir.model']._get('res.users'))
        attachment = self._upload(self.order, 'notes.docx')
        self.assertEqual(attachment.name, 'notes.docx')

    def test_fr_0013_inactive_pattern_keeps_uploaded_name(self):
        """Requirements: FR-AM-0013. An inactive pattern is no pattern."""
        self._make_rule('{record_name}', active=False)
        attachment = self._upload(self.order, 'notes.docx')
        self.assertEqual(attachment.name, 'notes.docx')

    def test_fr_0013_no_record_keeps_name(self):
        """Requirements: FR-AM-0013. A file attached to no record keeps its name."""
        self._make_rule('{record_name}')
        attachment = self.env['ir.attachment'].with_user(self.user).create(
            {'name': 'loose.txt', 'raw': b'am-loose'})
        self.assertEqual(attachment.name, 'loose.txt')

    # -- FR-AM-0017 ------------------------------------------------------
    def test_sc_0017_1_typed_name_is_kept(self):
        """Requirements: FR-AM-0017. SC-AM-0017-1: a name the uploader typed ("Signed
        contract") is kept exactly, with no extension added and no pattern applied."""
        self._make_rule('{record_ref}_{date}')
        # The creator must be able to write the contact, or native create refuses
        # before any naming applies; an administrator can.
        attachment = self.env['ir.attachment'].with_user(self.admin).create({
            'name': 'Signed contract', 'raw': b'am-signed',
            'res_model': 'res.partner', 'res_id': self.order.id})
        self.assertEqual(attachment.name, 'Signed contract')
