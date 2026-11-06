"""Acceptance tests of T-001: tracker settings (record types, followed fields, sensitive marks).

Written from the spec only. The entry-recording halves of SC-RCT-008-1, SC-RCT-008-2 and
SC-RCT-009-2 belong to the entry model of a later task; here the configuration side is checked.
"""
from odoo.exceptions import AccessError, ValidationError
from odoo.tests.common import TransactionCase, new_test_user
from odoo.tools import mute_logger

MODELS = ('res.partner', 'sale.order', 'purchase.order', 'product.template')


class TestSettingsAcceptance(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.admin = new_test_user(cls.env, login='rct_admin', groups='base.group_user,base.group_system')
        cls.marc = new_test_user(cls.env, login='rct_marc', groups='base.group_user')

    def setUp(self):
        super().setUp()
        self.Model = self.env['record.change.model']
        self.Field = self.env['record.change.field']

    def _type(self, model, user=None):
        rec = (self.Model.with_user(user) if user else self.Model).search([('model_id.model', '=', model)])
        self.assertEqual(len(rec), 1, "one setting line for %s" % model)
        return rec

    def _line(self, model, fname, user=None):
        rtype = self._type(model, user)
        return rtype.field_ids.filtered(lambda l: l.field_id.name == fname)

    def _fnames(self, model):
        return set(self._type(model).field_ids.filtered('is_followed').mapped('field_id.name'))

    def _ir_field(self, model, fname):
        return self.env['ir.model.fields']._get(model, fname)

    # ---- FR-RCT-007 / BR-RCT-002
    def test_007_1_exactly_four_record_types_listed(self):
        """Requirements: FR-RCT-007, BR-RCT-002. Scenario SC-RCT-007-1."""
        lines = self.Model.with_user(self.admin).search([])
        self.assertEqual(set(lines.mapped('model_id.model')), set(MODELS))
        self.assertEqual(len(lines), 4)

    def test_007_other_record_type_refused(self):
        """Requirements: FR-RCT-007, BR-RCT-002. A record type other than the four is refused."""
        other = self.env['ir.model']._get('res.users')
        with self.assertRaises(ValidationError):
            self.Model.with_user(self.admin).create({'model_id': other.id})

    def test_007_2_turn_following_off_for_product(self):
        """Requirements: FR-RCT-007. Scenario SC-RCT-007-2."""
        product = self._type('product.template', self.admin)
        self.assertTrue(product.is_followed)
        product.write({'is_followed': False})
        self.env.flush_all()
        self.assertFalse(self._type('product.template').is_followed)
        self.assertTrue(self._type('res.partner').is_followed)

    # ---- FR-RCT-008
    def test_008_1_add_field_website_to_contact(self):
        """Requirements: FR-RCT-008. Scenario SC-RCT-008-1 (configuration side)."""
        self.assertNotIn('website', self._fnames('res.partner'))
        rtype = self._type('res.partner', self.admin)
        self.Field.with_user(self.admin).create({
            'tracked_model_id': rtype.id,
            'field_id': self._ir_field('res.partner', 'website').id,
        })
        self.env.flush_all()
        line = self._line('res.partner', 'website')
        self.assertTrue(line.is_followed)
        self.assertIn('website', self._fnames('res.partner'))

    def test_008_2_remove_field_phone_from_contact(self):
        """Requirements: FR-RCT-008. Scenario SC-RCT-008-2 (configuration side)."""
        self.assertIn('phone', self._fnames('res.partner'))
        self._line('res.partner', 'phone', self.admin).unlink()
        self.env.flush_all()
        self.assertNotIn('phone', self._fnames('res.partner'))

    def test_008_duplicate_field_line_refused(self):
        """Requirements: FR-RCT-008. A field is one line per record type."""
        rtype = self._type('res.partner')
        with self.assertRaises(Exception), mute_logger('odoo.sql_db'), self.cr.savepoint():
            self.Field.create({
                'tracked_model_id': rtype.id,
                'field_id': self._ir_field('res.partner', 'email').id,
            })

    def test_008_field_of_another_record_type_refused(self):
        """Requirements: FR-RCT-008. A field must belong to the record type it is followed on."""
        rtype = self._type('res.partner')
        with self.assertRaises(ValidationError):
            self.Field.create({
                'tracked_model_id': rtype.id,
                'field_id': self._ir_field('sale.order', 'client_order_ref').id,
            })

    # ---- FR-RCT-009
    def test_009_1_starting_set_after_install(self):
        """Requirements: FR-RCT-009. Scenario SC-RCT-009-1."""
        lines = self.Model.with_user(self.admin).search([])
        self.assertEqual(len(lines), 4)
        self.assertTrue(all(lines.mapped('is_followed')))
        self.assertEqual(
            self._fnames('sale.order'),
            {'partner_id', 'user_id', 'payment_term_id', 'validity_date', 'state', 'amount_total'})
        self.assertFalse(self.Field.search([('is_sensitive', '=', True)]))

    def test_009_starting_sets_of_other_types(self):
        """Requirements: FR-RCT-009. Starting sets of contact, purchase order and product."""
        self.assertTrue({'name', 'email', 'phone', 'street', 'city', 'country_id', 'vat', 'user_id'}
                        <= self._fnames('res.partner'))
        self.assertTrue({'partner_id', 'user_id', 'date_order', 'date_planned', 'payment_term_id',
                         'state', 'amount_total'} <= self._fnames('purchase.order'))
        self.assertTrue({'name', 'default_code', 'list_price', 'categ_id', 'barcode'}
                        <= self._fnames('product.template'))

    def test_009_2_sales_price_followed_from_start(self):
        """Requirements: FR-RCT-009. Scenario SC-RCT-009-2 (configuration side): sales price is followed."""
        self.assertIn('list_price', self._fnames('product.template'))

    # ---- FR-RCT-010 / BR-RCT-003
    def test_010_1_technical_fields_not_offered(self):
        """Requirements: FR-RCT-010. Scenario SC-RCT-010-1."""
        field = self.Field._fields['field_id']
        self.assertTrue(field.domain, "the field choice carries a domain that restricts what is offered")
        rtype = self._type('res.partner')
        line = self.Field.new({'tracked_model_id': rtype.id})
        if callable(field.domain) or isinstance(field.domain, (list, tuple)):
            offered = self.env['ir.model.fields'].search(field.get_comodel_domain(line))
            names = set(offered.filtered(lambda f: f.model == 'res.partner').mapped('name'))
            self.assertNotIn('write_date', names)
            self.assertNotIn('create_uid', names)
            self.assertIn('phone', names)

    def test_010_2_technical_field_refused_on_create(self):
        """Requirements: FR-RCT-010, BR-RCT-003. Scenario SC-RCT-010-2."""
        rtype = self._type('res.partner', self.admin)
        for fname in ('write_date', 'create_uid', 'id', 'display_name', 'image_1920', 'child_ids', 'message_ids'):
            with self.subTest(field=fname):
                with self.assertRaisesRegex(ValidationError, 'Technical fields cannot be followed'):
                    with self.cr.savepoint():
                        self.Field.with_user(self.admin).create({
                            'tracked_model_id': rtype.id,
                            'field_id': self._ir_field('res.partner', fname).id,
                        })

    def test_010_2_technical_field_refused_on_write(self):
        """Requirements: FR-RCT-010. Scenario SC-RCT-010-2: also when an existing line is changed."""
        line = self._line('res.partner', 'phone', self.admin)
        with self.assertRaisesRegex(ValidationError, 'Technical fields cannot be followed'):
            line.write({'field_id': self._ir_field('res.partner', 'write_date').id})

    def test_010_technical_field_refused_when_only_marked_sensitive(self):
        """Requirements: FR-RCT-011, BR-RCT-003. Only non-technical fields can be marked sensitive."""
        rtype = self._type('res.partner', self.admin)
        with self.assertRaisesRegex(ValidationError, 'Technical fields cannot be followed'):
            self.Field.with_user(self.admin).create({
                'tracked_model_id': rtype.id,
                'field_id': self._ir_field('res.partner', 'write_date').id,
                'is_followed': False,
                'is_sensitive': True,
            })

    # ---- FR-RCT-011 / BR-RCT-004
    def test_011_1_mark_tax_id_sensitive(self):
        """Requirements: FR-RCT-011. Scenario SC-RCT-011-1."""
        line = self._line('res.partner', 'vat', self.admin)
        self.assertFalse(line.is_sensitive)
        line.write({'is_sensitive': True})
        self.env.flush_all()
        self.assertTrue(self._line('res.partner', 'vat').is_sensitive)

    def test_011_2_unmark_tax_id_sensitive(self):
        """Requirements: FR-RCT-011. Scenario SC-RCT-011-2."""
        line = self._line('res.partner', 'vat', self.admin)
        line.write({'is_sensitive': True})
        line.write({'is_sensitive': False})
        self.env.flush_all()
        self.assertFalse(self._line('res.partner', 'vat').is_sensitive)

    def test_011_mark_unfollowed_field_sensitive(self):
        """Requirements: FR-RCT-011. A field can be marked sensitive whether or not it is followed."""
        rtype = self._type('res.partner', self.admin)
        line = self.Field.with_user(self.admin).create({
            'tracked_model_id': rtype.id,
            'field_id': self._ir_field('res.partner', 'comment').id,
            'is_followed': False,
            'is_sensitive': True,
        })
        self.assertTrue(line.is_sensitive)
        self.assertFalse(line.is_followed)

    # ---- FR-RCT-014 / BR-RCT-001
    def test_014_1_other_user_cannot_remove_followed_field(self):
        """Requirements: FR-RCT-014, BR-RCT-001. Scenario SC-RCT-014-1."""
        line = self._line('res.partner', 'phone', self.marc)
        with self.assertRaises(AccessError):
            line.unlink()
        self.env.flush_all()
        self.assertIn('phone', self._fnames('res.partner'))

    def test_014_1_other_user_cannot_change_settings_otherwise(self):
        """Requirements: FR-RCT-014, BR-RCT-001. Create, unfollow and record-type switch are refused too."""
        line = self._line('res.partner', 'phone', self.marc)
        with self.assertRaises(AccessError):
            line.write({'is_followed': False})
        rtype = self._type('res.partner', self.marc)
        with self.assertRaises(AccessError):
            rtype.write({'is_followed': False})
        with self.assertRaises(AccessError):
            self.Field.with_user(self.marc).create({
                'tracked_model_id': rtype.id,
                'field_id': self._ir_field('res.partner', 'website').id,
            })
        self.env.flush_all()
        self.assertTrue(self._type('res.partner').is_followed)
        self.assertIn('phone', self._fnames('res.partner'))

    def test_014_2_other_user_cannot_mark_sensitive(self):
        """Requirements: FR-RCT-014, BR-RCT-001. Scenario SC-RCT-014-2."""
        line = self._line('res.partner', 'email', self.marc)
        with self.assertRaises(AccessError):
            line.write({'is_sensitive': True})
        self.env.flush_all()
        self.assertFalse(self._line('res.partner', 'email').is_sensitive)

    def test_014_3_other_user_can_read_settings(self):
        """Requirements: FR-RCT-014. Scenario SC-RCT-014-3."""
        lines = self.Model.with_user(self.marc).search([])
        self.assertEqual(set(lines.mapped('model_id.model')), set(MODELS))
        partner = lines.filtered(lambda l: l.model_id.model == 'res.partner')
        self.assertIn('phone', partner.field_ids.mapped('field_id.name'))
