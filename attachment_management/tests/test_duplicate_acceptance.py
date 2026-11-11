"""Acceptance tests of T-006: warning on upload of a file already stored, never blocking.

Requirements: BR-AM-0003, FR-AM-0014, FR-AM-0015, FR-AM-0016.

Sales orders and employees are not installed with this module, so the scenarios
run on Contact (res.partner): an ordinary contact stands for the contact and the
sales order, and a contact hidden from every user by a restricting access record stands for
the employee record the user cannot open. The warning is the bus event
``attachment_management/duplicate`` the design sends to the uploader; the tests
read it from the bus table and look for the copies' names in its payload, never
for a particular payload key. The Open buttons and the notification itself are
browser work that the build leaves to the JS service; the upload is the ORM
creation of the attachment by the uploader, which is what the upload routes do.
"""
import json

from odoo.tests.common import TransactionCase, new_test_user, tagged

DUPLICATE_EVENT = 'attachment_management/duplicate'


@tagged('post_install', '-at_install')
class TestDuplicateAcceptance(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        groups = 'base.group_user,base.group_partner_manager'
        cls.user = new_test_user(cls.env, login='am_dup_user', groups=groups)
        cls.contact = cls.env['res.partner'].create({'name': 'DupVisibleContact'})
        cls.order = cls.env['res.partner'].create({'name': 'DupTargetOrder'})
        cls.hidden = cls.env['res.partner'].create({'name': 'DupHiddenEmployee'})
        cls.env['ir.access'].create({
            'name': 'am_dup_hide',
            'model_id': cls.env['ir.model']._get('res.partner').id,
            'operation': 'r',
            'domain': [('name', '!=', 'DupHiddenEmployee')],
        })
        cls.env.transaction.invalidate_access_cache()
        cls.content = b'am-duplicate-content-one'

    def _store(self, record, name, content=None, user=None):
        """A copy stored beforehand (as the given user, an administrator by default)."""
        env = self.env(user=user) if user else self.env(su=True)
        return env['ir.attachment'].create({
            'name': name,
            'raw': content or self.content,
            'res_model': record._name,
            'res_id': record.id,
        })

    def _reset_bus(self):
        self.env.cr.precommit.data.get('bus.bus.values', []).clear()
        self.env['bus.bus'].sudo().search([]).unlink()

    def _upload(self, record, name, content=None):
        """The user uploads a file to a record the way the upload screens do (the
        ``am_name_from_file`` marker); returns (attachment, duplicate payload text list)."""
        self._reset_bus()
        attachment = self.env['ir.attachment'].with_user(self.user).with_context(
            am_name_from_file=True).create({
            'name': name,
            'raw': content or self.content,
            'res_model': record._name,
            'res_id': record.id,
        })
        self.env.flush_all()
        self.env.cr.precommit.run()
        warnings = []
        for notif in self.env['bus.bus'].sudo().search([]):
            message = json.loads(notif.message)
            if message.get('type') == DUPLICATE_EVENT:
                warnings.append((notif.channel, json.dumps(message.get('payload'))))
        return attachment, warnings

    def _own(self, warnings):
        """Payload texts of the warnings sent to the uploader."""
        own = []
        for channel, text in warnings:
            parts = json.loads(channel) if isinstance(channel, str) else list(channel)
            if parts[-2:] == ['res.users', self.user.id]:
                own.append(text)
        return own

    # -- FR-AM-0014 / BR-AM-0003 -----------------------------------------
    def test_sc_0014_1_renamed_copy_raises_warning(self):
        """Requirements: FR-AM-0014, BR-AM-0003. SC-AM-0014-1: the same content
        uploaded under another name to another record warns the uploader that the
        file is already stored."""
        self._store(self.contact, 'contract.pdf')
        _attachment, warnings = self._upload(self.order, 'copy.pdf')
        own = self._own(warnings)
        self.assertEqual(len(own), 1, "Exactly one duplicate warning for the uploader expected")
        self.assertIn('already stored', own[0])

    def test_sc_0014_2_same_name_other_content_no_warning(self):
        """Requirements: FR-AM-0014, BR-AM-0003. SC-AM-0014-2: a different file with
        the same name is not a duplicate; no warning."""
        self._store(self.contact, 'contract.pdf')
        _attachment, warnings = self._upload(self.order, 'contract.pdf', b'am-duplicate-other-content')
        self.assertEqual(warnings, [], "A different file must raise no warning")

    def test_first_upload_of_a_file_raises_no_warning(self):
        """Requirements: FR-AM-0014. A file whose content is not stored yet raises no warning."""
        _attachment, warnings = self._upload(self.order, 'fresh.pdf', b'am-duplicate-never-seen')
        self.assertEqual(warnings, [])

    # -- FR-AM-0015 -------------------------------------------------------
    def test_sc_0015_1_upload_still_completes(self):
        """Requirements: FR-AM-0015. SC-AM-0015-1: after the warning the uploaded
        file stays attached to its record (the closing of the notification is
        browser work)."""
        self._store(self.contact, 'contract.pdf')
        attachment, warnings = self._upload(self.order, 'copy.pdf')
        self.assertTrue(self._own(warnings), "The warning was not raised")
        self.assertTrue(attachment.exists())
        self.assertEqual(attachment.res_model, 'res.partner')
        self.assertEqual(attachment.res_id, self.order.id)
        found = self.env['ir.attachment'].sudo().search([
            ('res_model', '=', 'res.partner'), ('res_id', '=', self.order.id), ('name', '=', 'copy.pdf')])
        self.assertEqual(found, attachment.sudo())

    # -- FR-AM-0016 -------------------------------------------------------
    def test_sc_0016_1_warning_names_only_openable_copy(self):
        """Requirements: FR-AM-0016. SC-AM-0016-1: the warning names the contact the
        user can open and not the record the user cannot open."""
        self._store(self.contact, 'contract.pdf')
        self._store(self.hidden, 'secret.pdf')
        _attachment, warnings = self._upload(self.order, 'copy.pdf')
        own = self._own(warnings)
        self.assertEqual(len(own), 1)
        self.assertIn('DupVisibleContact', own[0])
        self.assertNotIn('DupHiddenEmployee', own[0])
        self.assertNotIn('secret.pdf', own[0])

    def test_sc_0016_2_no_openable_copy_says_only_already_stored(self):
        """Requirements: FR-AM-0016. SC-AM-0016-2: when the user can open none of the
        copies the warning only says the file is already stored."""
        self._store(self.hidden, 'secret.pdf')
        _attachment, warnings = self._upload(self.order, 'copy.pdf')
        own = self._own(warnings)
        self.assertEqual(len(own), 1, "The warning must still be shown")
        self.assertIn('already stored', own[0])
        self.assertNotIn('DupHiddenEmployee', own[0])
        self.assertNotIn('secret.pdf', own[0])

    def test_boundary_five_copies_all_named_six_capped(self):
        """Requirements: FR-AM-0016. Boundary: up to five openable copies are named;
        a sixth is not."""
        records = self.env['res.partner'].create(
            [{'name': 'DupCopyHolder%d' % i} for i in range(6)])
        for record in records[:5]:
            self._store(record, 'c.pdf')
        _a, warnings = self._upload(self.order, 'copy.pdf')
        text = self._own(warnings)[0]
        named = [r.name for r in records[:5] if r.name in text]
        self.assertEqual(len(named), 5, "Five openable copies must all be named")
        self._store(records[5], 'c.pdf')
        _a, warnings = self._upload(self.order, 'copy2.pdf')
        text = self._own(warnings)[0]
        named = [r.name for r in records if r.name in text]
        self.assertEqual(len(named), 5, "At most five copies are named")

    def test_internal_picture_field_files_are_not_compared(self):
        """Requirements: FR-AM-0014. A file stored for a picture field (res_field set)
        is not a copy to warn about."""
        self.env['ir.attachment'].sudo().create({
            'name': 'image_1920', 'raw': self.content,
            'res_model': 'res.partner', 'res_id': self.contact.id, 'res_field': 'image_1920'})
        _attachment, warnings = self._upload(self.order, 'copy.pdf')
        self.assertEqual(warnings, [])

    def test_file_nobody_uploaded_sends_no_warning(self):
        """Requirements: FR-AM-0014. Answered decision: warn only on files a person
        uploads. A copy made by another app (same content, no upload marker) raises no warning."""
        self._store(self.contact, 'contract.pdf')
        self._reset_bus()
        self.env['ir.attachment'].with_user(self.user).create({
            'name': 'copied.pdf', 'raw': self.content,
            'res_model': 'res.partner', 'res_id': self.order.id})
        self.env.flush_all()
        self.env.cr.precommit.run()
        events = [json.loads(n.message).get('type') for n in self.env['bus.bus'].sudo().search([])]
        self.assertNotIn(DUPLICATE_EVENT, events)
