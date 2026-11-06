"""Acceptance tests of T-008: the tracker follows the import's 'track history' option.

Written from the spec only. Requirements: FR-RCT-016.

The import screen sends the option as the context key ``tracking_disable``: True when
'track history' is off, False when it is ticked; the import calls ``load`` with
``import_file`` set.
"""
from odoo.tests.common import TransactionCase, new_test_user


class TestImportOptionAcceptance(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.admin_user = new_test_user(
            cls.env, login='rct_imp_admin', name='Import Admin',
            groups='base.group_user,base.group_system,base.group_partner_manager')
        rtype = cls.env['record.change.model'].sudo().search([('model_id.model', '=', 'res.partner')])
        ir_field = cls.env['ir.model.fields']._get('res.partner', 'phone')
        line = rtype.field_ids.filtered(lambda l: l.field_id == ir_field)
        vals = {'is_followed': True, 'is_sensitive': False}
        if line:
            line.sudo().write(vals)
        else:
            cls.env['record.change.field'].sudo().create(
                dict(vals, tracked_model_id=rtype.id, field_id=ir_field.id))
        rtype.sudo().write({'is_followed': True})
        cls.partner = cls.env['res.partner'].sudo().create({'name': 'Azure Interior', 'phone': '+32 1'})
        cls.xmlid = cls.env['ir.model.data'].sudo().create({
            'module': '__rct_import__', 'name': 'azure_interior',
            'model': 'res.partner', 'res_id': cls.partner.id}).complete_name
        cls.env.flush_all()

    def _entries(self):
        self.env.flush_all()
        return self.env['record.change.entry'].sudo().search([
            ('res_model', '=', 'res.partner'), ('res_id', '=', self.partner.id),
            ('field_name', '=', 'phone')])

    def _import(self, tracking_disable):
        model = self.env['res.partner'].with_user(self.admin_user).with_context(
            import_file=True, tracking_disable=tracking_disable)
        result = model.load(['id', 'phone'], [[self.xmlid, '+32 2 555 0100']])
        self.assertFalse(result.get('messages'), result.get('messages'))
        self.env.flush_all()
        self.assertEqual(self.partner.phone, '+32 2 555 0100')

    def test_016_01_import_with_track_history_off_records_nothing(self):
        """Requirements: FR-RCT-016. Scenario SC-RCT-016-01."""
        self._import(tracking_disable=True)
        self.assertFalse(self._entries())

    def test_016_02_import_with_track_history_ticked_records_one_entry(self):
        """Requirements: FR-RCT-016. Scenario SC-RCT-016-02."""
        self._import(tracking_disable=False)
        entries = self._entries()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries.old_value, '+32 1')
        self.assertEqual(entries.new_value, '+32 2 555 0100')
