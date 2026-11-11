from odoo.exceptions import ValidationError
from odoo.tests.common import tagged

from .common import AttachmentManagementCase


@tagged('post_install', '-at_install')
class TestNamingRule(AttachmentManagementCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner_model = cls.env['ir.model']._get('res.partner')
        cls.partner = cls.env['res.partner'].create({'name': 'Con/tact', 'ref': 'C:1'})

    def _rule(self, pattern, **kwargs):
        return self.env['attachment.naming.rule'].with_user(self.am_admin).create(
            dict(model_id=self.partner_model.id, pattern=pattern, **kwargs))

    def _upload(self, vals=None, **context):
        values = dict({'name': 'scan.pdf', 'raw': b'am-nm', 'res_model': 'res.partner',
                       'res_id': self.partner.id}, **(vals or {}))
        return self.env['ir.attachment'].with_user(self.am_user).sudo().with_context(**context).create(values)

    def test_placeholders_render_and_sanitise(self):
        """Requirements: FR-AM-0012. Each placeholder renders; forbidden characters become underscores."""
        self._rule('{record_name}|{record_ref}|{uploader}|{original_name}|{seq}')
        attachment = self._upload(am_name_from_file=True)
        self.assertEqual(attachment.name, 'Con_tact_Con_tact_%s_scan_1.pdf' % self.am_user.name)

    def test_running_number_increments(self):
        """Requirements: FR-AM-0011. {seq} is the next number of the rule's own sequence."""
        self._rule('doc{seq}')
        first = self._upload(am_name_from_file=True)
        second = self._upload({'raw': b'am-nm2'}, am_name_from_file=True)
        self.assertEqual((first.name, second.name), ('doc1.pdf', 'doc2.pdf'))

    def test_name_is_cut_at_the_limit(self):
        """Requirements: FR-AM-0012. A long rendered name is cut at 200 characters before the extension."""
        self._rule('x' * 300)
        self.assertEqual(len(self._upload(am_name_from_file=True).name), 200 + len('.pdf'))

    def test_extension_comes_from_mimetype_when_name_has_none(self):
        """Requirements: FR-AM-0012. A file name with no extension gets the one of its mimetype."""
        self._rule('{record_ref}')
        attachment = self._upload({'name': 'scan', 'mimetype': 'application/pdf'}, am_name_from_file=True)
        self.assertEqual(attachment.name, 'Con_tact.pdf')

    def test_without_upload_flag_the_name_is_kept(self):
        """Requirements: FR-AM-0012. Files the system creates (no flag) keep their own name."""
        self._rule('{record_name}')
        self.assertEqual(self._upload().name, 'scan.pdf')

    def test_field_file_is_not_renamed(self):
        """Requirements: FR-AM-0013. A file of a binary field keeps its name."""
        self._rule('{record_name}')
        attachment = self._upload({'res_field': 'image_1920'}, am_name_from_file=True)
        self.assertEqual(attachment.name, 'scan.pdf')

    def test_pending_composer_upload_named_when_posted(self):
        """Requirements: FR-AM-0012. A pending composer file is named when it lands on its record."""
        self._rule('{record_ref}')
        pending = self._upload({'res_model': 'mail.compose.message', 'res_id': 0}, am_name_from_file=True)
        self.assertEqual(pending.name, 'scan.pdf')
        pending.write({'res_model': 'res.partner', 'res_id': self.partner.id})
        self.assertEqual(pending.name, 'Con_tact.pdf')

    def test_pending_file_without_pattern_moves_unchanged(self):
        """Requirements: FR-AM-0013. With no pattern the moved composer file keeps its name."""
        pending = self._upload({'res_model': 'mail.compose.message', 'res_id': 0}, am_name_from_file=True)
        pending.write({'res_model': 'res.partner', 'res_id': self.partner.id})
        self.assertEqual((pending.name, pending.res_id), ('scan.pdf', self.partner.id))

    def test_reference_falls_back_to_model_and_id(self):
        """Requirements: FR-AM-0012. A record with no usable name field value is referenced by kind and id."""
        nameless = self.env['res.partner'].create({'name': False, 'type': 'other'})
        reference = self.env['attachment.naming.rule']._record_reference(nameless)
        self.assertEqual(reference, 'Contact %s' % nameless.id)

    def test_pattern_must_render_a_name(self):
        """Requirements: FR-AM-0011. A pattern that renders nothing is refused."""
        with self.assertRaises(ValidationError):
            self._rule('   ')

    def test_second_active_pattern_refused_even_when_saved_by_code(self):
        """Requirements: FR-AM-0011. One active pattern per kind of record; an archived one does not count."""
        self._rule('{record_name}')
        with self.assertRaises(ValidationError):
            self._rule('{record_ref}')
        self.assertFalse(self._rule('{record_ref}', active=False).active)

    def test_transient_model_refused(self):
        """Requirements: FR-AM-0011. Only models that store records can have a pattern."""
        with self.assertRaises(ValidationError):
            self.env['attachment.naming.rule'].with_user(self.am_admin).create({
                'model_id': self.env['ir.model']._get('mail.compose.message').id, 'pattern': 'x'})

    def test_copy_is_archived_and_sequence_goes_with_the_rule(self):
        """Requirements: FR-AM-0011. A copy is archived; deleting a rule deletes its sequence."""
        rule = self._rule('{record_name}')
        self.assertFalse(rule.copy().active)
        sequence = rule.sequence_id
        rule.unlink()
        self.assertFalse(sequence.exists())

    def test_example_shows_rendered_name(self):
        """Requirements: FR-AM-0011. The form shows an example of the pattern's result."""
        self.assertEqual(self._rule('{record_ref}').example_name, 'AZ-001.pdf')
