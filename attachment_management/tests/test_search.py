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
        self.env['ir.attachment']._cron_refresh_record_labels()
        self.assertEqual(att.record_label, 'Label Renamed')

    def test_no_record_means_no_label(self):
        """Requirements: FR-AM-0003. A file with no record has no kind of record and no label."""
        att = self.env['ir.attachment'].create({'name': 'n.txt', 'raw': b'n'})
        self.assertFalse(att.res_model_id)
        self.assertFalse(att.record_label)
