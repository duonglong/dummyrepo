from odoo.fields import Domain
from odoo.tests.common import tagged

from ..models.ir_attachment import file_type_group_of
from .common import AttachmentManagementCase


@tagged('post_install', '-at_install')
class TestSearchHelpers(AttachmentManagementCase):

    def test_type_group_table(self):
        """Requirements: FR-AM-0005. Each mimetype family maps to one group, unknown ones to other."""
        expected = {
            'application/pdf': 'pdf',
            'image/png': 'image',
            'application/vnd.openxmlformats-officedocument.wordprocessingml.document': 'document',
            'text/csv': 'spreadsheet',
            'application/vnd.ms-powerpoint': 'presentation',
            'application/x-7z-compressed': 'archive',
            'application/acad': 'other',
            '': 'other',
            False: 'other',
        }
        for mimetype, group in expected.items():
            self.assertEqual(file_type_group_of(mimetype), group, mimetype)

    def test_mirror_adds_res_model_condition(self):
        """Requirements: NFR-AM-0001. A kind-of-record condition also names the model for native's SQL access path."""
        model_id = self.env['ir.model']._get_id('res.partner')
        domain = Domain([('res_model_id', '=', model_id)]).map_conditions(
            self.env['ir.attachment']._mirror_res_model_condition)
        fields_named = {c.field_expr for c in domain.iter_conditions()}
        self.assertEqual(fields_named, {'res_model_id', 'res_model'})

    def test_record_label_follows_the_record(self):
        """Requirements: FR-AM-0004. The label is set at create, on a move and by the nightly job."""
        partner = self.env['res.partner'].create({'name': 'Label One'})
        other = self.env['res.partner'].create({'name': 'Label Two'})
        att = self.env['ir.attachment'].create({
            'name': 'l.txt', 'raw': b'l', 'res_model': 'res.partner', 'res_id': partner.id})
        self.assertEqual(att.record_label, 'Label One')
        att.write({'res_id': other.id})
        self.assertEqual(att.record_label, 'Label Two')
        other.name = 'Label Renamed'
        self.assertEqual(att.record_label, 'Label Two')
        self.env['ir.attachment']._refresh_all_record_labels()
        self.assertEqual(att.record_label, 'Label Renamed')

    def test_no_record_means_no_label(self):
        """Requirements: FR-AM-0003. A file with no record has no kind of record and no label."""
        att = self.env['ir.attachment'].create({'name': 'n.txt', 'raw': b'n'})
        self.assertFalse(att.res_model_id)
        self.assertFalse(att.record_label)

    def test_detached_file_loses_its_label(self):
        """Requirements: FR-AM-0004. A file taken off its record keeps no record name, also after the nightly job."""
        partner = self.env['res.partner'].create({'name': 'Label Gone'})
        att = self.env['ir.attachment'].create({
            'name': 'd.txt', 'raw': b'd', 'res_model': 'res.partner', 'res_id': partner.id})
        self.assertEqual(att.record_label, 'Label Gone')
        att.write({'res_model': False, 'res_id': 0})
        self.assertFalse(att.record_label)
        stale = self.env['ir.attachment'].create({'name': 's.txt', 'raw': b's'})
        stale.write({'record_label': 'Stale'})
        self.env['ir.attachment']._refresh_all_record_labels()
        self.assertFalse(stale.record_label)

    def test_labels_of_many_files_in_one_refresh(self):
        """Requirements: FR-AM-0004. A batch of files on several records gets one label each."""
        partners = self.env['res.partner'].create([{'name': 'Batch A'}, {'name': 'Batch B'}])
        atts = self.env['ir.attachment'].create([
            {'name': f'b{i}.txt', 'raw': b'b', 'res_model': 'res.partner', 'res_id': partners[i % 2].id}
            for i in range(4)])
        self.assertEqual(atts.mapped('record_label'), ['Batch A', 'Batch B', 'Batch A', 'Batch B'])

    def test_field_files_get_no_label(self):
        """Requirements: FR-AM-0001. Internal field files stay out of the label work."""
        partner = self.env['res.partner'].create({'name': 'Field Owner'})
        att = self.env['ir.attachment'].create({
            'name': 'f', 'raw': b'f', 'res_model': 'res.partner', 'res_id': partner.id,
            'res_field': 'image_1920'})
        self.assertFalse(att.record_label)

    def test_unreadable_kind_left_out_but_own_file_without_record_kept(self):
        """Requirements: BR-AM-0001, BR-AM-0002, NFR-AM-0001. Leaving out the kinds a user cannot read keeps native's result.

        A file on a record of a kind the user cannot read stays hidden; a file of that kind
        with no record stays with its uploader, as native decides by the uploader there.
        """
        parameter = self.env['ir.config_parameter'].sudo().search([], limit=1)
        attachments = self.env['ir.attachment'].with_user(self.am_user).sudo()
        on_record, no_record = attachments.create([
            {'name': 'am-kind-guard.txt', 'raw': b'on', 'res_model': 'ir.config_parameter', 'res_id': parameter.id},
            {'name': 'am-kind-guard.txt', 'raw': b'off', 'res_model': 'ir.config_parameter', 'res_id': 0},
        ])
        found = self.env['ir.attachment'].with_user(self.am_user).search([('name', '=', 'am-kind-guard.txt')])
        self.assertEqual(found, no_record)
        self.assertNotIn(on_record, found)
        self.assertEqual(
            self.env['ir.attachment'].with_user(self.am_user).search_count([('name', '=', 'am-kind-guard.txt')]), 1)
