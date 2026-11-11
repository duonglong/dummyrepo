"""Own tests of T-006: the duplicate warning, with real file content (``raw``).

Odoo 20 ignores ``datas`` on create, so these tests give the content as ``raw``.
Requirements: BR-AM-0003, FR-AM-0014, FR-AM-0015, FR-AM-0016.
"""
import json

from odoo.tests.common import TransactionCase, new_test_user, tagged

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
