from odoo import Command
from odoo.tests.common import TransactionCase


class TestOnSaveAcceptance(TransactionCase):
    """Acceptance tests of T-002 (matching engine, sets, on-save warning).

    Requirements: BR-DRD-0001, BR-DRD-0002, BR-DRD-0003, BR-DRD-0004, BR-DRD-0005,
    FR-DRD-0003, FR-DRD-0004.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        groups = cls.env.ref('base.group_user') | cls.env.ref('base.group_partner_manager')
        groups |= cls.env.ref('product.group_product_manager')
        cls.saver = cls.env['res.users'].create({
            'name': 'Saver', 'login': 'saver_t002',
            'group_ids': [Command.set(groups.ids)],
        })
        cls.Partner = cls.env['res.partner'].with_user(cls.saver)
        cls.Product = cls.env['product.template'].with_user(cls.saver)

    # helpers
    def _rule(self, conditions, name='T002 rule', model='res.partner', **extra):
        vals = {
            'name': name,
            'model_id': self.env['ir.model']._get(model).id,
            'condition_ids': [Command.create({
                'field_id': self.env['ir.model.fields']._get(model, f).id,
                'match_type': m,
            }) for f, m in conditions],
        }
        vals.update(extra)
        return self.env['duplicate.rule'].sudo().create(vals)

    def _open_sets(self, rule):
        self.env.flush_all()
        return self.env['duplicate.group'].sudo().search(
            [('rule_id', '=', rule.id), ('state', '=', 'open')])

    def _members(self, group):
        return set(group.line_ids.mapped('res_id'))

    def _bus_mark(self):
        return self.env['bus.bus'].sudo().search([], order='id desc', limit=1).id or 0

    def _bus_texts(self, mark):
        return [m.message for m in self.env['bus.bus'].sudo().search([('id', '>', mark)])]

    def _warned(self, mark, *needles):
        return any(all(n in t for n in needles) and 'simple_notification' in t
                   for t in self._bus_texts(mark))

    # SC-DRD-0003-1 / FR-DRD-0004
    def test_sc_0003_1_save_keeps_record_sets_and_warns(self):
        """Requirements: FR-DRD-0003, FR-DRD-0004, BR-DRD-0001. Scenario SC-DRD-0003-1."""
        rule = self._rule([('email', 'exact')], name='T002 same e-mail')
        old = self.Partner.create({'name': 'ACME Ltd', 'email': 'info@acme-t002.com'})
        mark = self._bus_mark()
        new = self.Partner.create({'name': 'Acme', 'email': 'INFO@acme-t002.com '})
        self.assertTrue(new.exists())
        sets = self._open_sets(rule)
        self.assertEqual(len(sets), 1)
        self.assertEqual(self._members(sets), {old.id, new.id})
        self.assertTrue(self._warned(mark, 'ACME Ltd', 'T002 same e-mail'),
                        "a warning naming 'ACME Ltd' and the rule must be sent")

    def test_sc_0003_2_no_match_no_warning(self):
        """Requirements: FR-DRD-0003. Scenario SC-DRD-0003-2."""
        rule = self._rule([('email', 'exact')], name='T002 quiet rule')
        self.Partner.create({'name': 'Other', 'email': 'a@one-t002.com'})
        mark = self._bus_mark()
        new = self.Partner.create({'name': 'Lone', 'email': 'b@two-t002.com'})
        self.assertTrue(new.exists())
        self.assertFalse(self._open_sets(rule))
        self.assertFalse(self._warned(mark, 'T002 quiet rule'))

    def test_sc_0003_3_archived_duplicate_ignored(self):
        """Requirements: FR-DRD-0003, BR-DRD-0004. Scenario SC-DRD-0003-3."""
        rule = self._rule([('email', 'exact')], name='T002 archived rule')
        self.Partner.create({
            'name': 'Gone', 'email': 'info@arch-t002.com', 'active': False})
        mark = self._bus_mark()
        self.Partner.create({'name': 'Fresh', 'email': 'info@arch-t002.com'})
        self.assertFalse(self._open_sets(rule))
        self.assertFalse(self._warned(mark, 'T002 archived rule'))

    def test_empty_value_never_matches(self):
        """Requirements: BR-DRD-0001. Two empty e-mails are not duplicates."""
        rule = self._rule([('email', 'exact')], name='T002 empty rule')
        self.Partner.create({'name': 'NoMail One T002'})
        self.Partner.create({'name': 'NoMail Two T002'})
        self.assertFalse(self._open_sets(rule))

    def test_inactive_rule_gives_nothing(self):
        """Requirements: FR-DRD-0003, FR-DRD-0004. A switched-off rule never reports."""
        rule = self._rule([('email', 'exact')], name='T002 off rule', active=False)
        self.Partner.create({'name': 'One', 'email': 'dup@off-t002.com'})
        mark = self._bus_mark()
        self.Partner.create({'name': 'Two', 'email': 'dup@off-t002.com'})
        self.assertFalse(self.env['duplicate.group'].sudo().search(
            [('rule_id', '=', rule.id)]))
        self.assertFalse(self._warned(mark, 'T002 off rule'))

    def test_write_of_rule_field_checks(self):
        """Requirements: FR-DRD-0004. Changing a rule field to a duplicate value is caught."""
        rule = self._rule([('email', 'exact')], name='T002 write rule')
        a = self.Partner.create({'name': 'WA', 'email': 'wa@write-t002.com'})
        b = self.Partner.create({'name': 'WB', 'email': 'wb@write-t002.com'})
        self.assertFalse(self._open_sets(rule))
        b.write({'email': 'WA@write-t002.com'})
        sets = self._open_sets(rule)
        self.assertEqual(len(sets), 1)
        self.assertEqual(self._members(sets), {a.id, b.id})

    # SC-DRD-0004-1
    def test_sc_0004_1_import_records_set_silently(self):
        """Requirements: FR-DRD-0004. Scenario SC-DRD-0004-1: import gives set, no warning."""
        rule = self._rule([('default_code', 'exact')], name='T002 ref rule',
                          model='product.template')
        first = self.Product.create({'name': 'Prod A', 'default_code': 'A-100-T002'})
        mark = self._bus_mark()
        second = self.Product.with_context(import_file=True).create(
            {'name': 'Prod B', 'default_code': 'a-100-t002'})
        sets = self._open_sets(rule)
        self.assertEqual(len(sets), 1)
        self.assertEqual(self._members(sets), {first.id, second.id})
        self.assertEqual(set(sets.line_ids.mapped('res_model')), {'product.template'})
        self.assertFalse(self._warned(mark, 'T002 ref rule'),
                         "an import has no screen: no warning is sent")

    # SC-DRD-0004-2
    def test_sc_0004_2_not_duplicates_not_reported_again(self):
        """Requirements: FR-DRD-0004, FR-DRD-0008. Scenario SC-DRD-0004-2."""
        rule = self._rule([('email', 'exact')], name='T002 Same e-mail nd')
        a = self.Partner.create({'name': 'NA', 'email': 'x@nd-t002.com'})
        b = self.Partner.create({'name': 'NB', 'email': 'x@nd-t002.com'})
        sets = self._open_sets(rule)
        self.assertEqual(len(sets), 1)
        sets.write({'state': 'not_duplicate'})
        mark = self._bus_mark()
        b.write({'email': 'x@nd-t002.com'})
        self.assertFalse(self._open_sets(rule))
        self.assertFalse(self._warned(mark, 'NA'))
        self.assertTrue(a.exists())

    def test_existing_open_set_grows(self):
        """Requirements: FR-DRD-0004, BR-DRD-0005. A third match grows the set."""
        rule = self._rule([('email', 'exact')], name='T002 grow rule')
        a = self.Partner.create({'name': 'GA', 'email': 'g@grow-t002.com'})
        b = self.Partner.create({'name': 'GB', 'email': 'g@grow-t002.com'})
        c = self.Partner.create({'name': 'GC', 'email': 'G@grow-t002.com'})
        sets = self._open_sets(rule)
        self.assertEqual(len(sets), 1)
        self.assertEqual(self._members(sets), {a.id, b.id, c.id})
        self.assertEqual(sets.record_count, 3)

    # BR-DRD-0002 / BR-DRD-0003 / BR-DRD-0005
    def test_sc_0001_2_partial_name_and_exact_city(self):
        """Requirements: BR-DRD-0002, BR-DRD-0003. Scenario SC-DRD-0001-2."""
        rule = self._rule([('name', 'partial'), ('city', 'exact')], name='T002 name city')
        old = self.Partner.create({'name': 'Zorblax Trading', 'city': 'Lyon'})
        paris = self.Partner.create({'name': 'Zorblax', 'city': 'Paris'})
        self.assertFalse(self._open_sets(rule), "a shared name alone is not enough")
        lyon = self.Partner.create({'name': 'Zorblax', 'city': 'lyon '})
        sets = self._open_sets(rule)
        self.assertEqual(len(sets), 1)
        self.assertEqual(self._members(sets), {old.id, lyon.id})
        self.assertNotIn(paris.id, self._members(sets))

    def test_partial_needs_three_characters(self):
        """Requirements: BR-DRD-0002. Boundary: 2 chars no match, 3 chars match."""
        rule = self._rule([('name', 'partial')], name='T002 partial len')
        self.Partner.create({'name': 'Qzfloop Inc'})
        self.Partner.create({'name': 'Qz'})
        self.assertFalse(self._open_sets(rule), "2 characters never match partly")
        short3 = self.Partner.create({'name': ' QZF '})
        sets = self._open_sets(rule)
        self.assertEqual(len(sets), 1)
        self.assertIn(short3.id, self._members(sets))

    def test_exact_condition_is_not_contains(self):
        """Requirements: BR-DRD-0001. Exact does not mean 'contains'."""
        rule = self._rule([('name', 'exact')], name='T002 exact only')
        self.Partner.create({'name': 'Vexnor'})
        self.Partner.create({'name': 'Vexnor Trading'})
        self.assertFalse(self._open_sets(rule))

    def test_many2one_exact_same_record(self):
        """Requirements: BR-DRD-0001. A link field matches on the same record."""
        rule = self._rule([('country_id', 'exact'), ('name', 'exact')],
                          name='T002 country')
        be = self.env.ref('base.be')
        fr = self.env.ref('base.fr')
        self.Partner.create({'name': 'Linkco', 'country_id': be.id})
        self.Partner.create({'name': 'Linkco', 'country_id': fr.id})
        self.assertFalse(self._open_sets(rule))
        self.Partner.create({'name': 'linkco', 'country_id': be.id})
        self.assertEqual(len(self._open_sets(rule)), 1)

    def test_companies_are_kept_apart_but_shared_matches_any(self):
        """Requirements: BR-DRD-0004. Different companies never match; shared matches any."""
        rule = self._rule([('name', 'exact')], name='T002 company rule')
        co_a = self.env['res.company'].create({'name': 'T002 Co A'})
        co_b = self.env['res.company'].create({'name': 'T002 Co B'})
        P = self.env['res.partner'].sudo()
        a = P.create({'name': 'Cosep', 'company_id': co_a.id})
        b = P.create({'name': 'Cosep', 'company_id': co_b.id})
        self.assertFalse(self._open_sets(rule))
        shared = P.create({'name': 'Cosep', 'company_id': False})
        members = [self._members(s) for s in self._open_sets(rule)]
        self.assertTrue(any(shared.id in m and (a.id in m or b.id in m) for m in members))
        for m in members:
            self.assertFalse({a.id, b.id} <= m, "two companies never share a set")

    def test_linked_matches_form_one_set(self):
        """Requirements: BR-DRD-0005. A~B and B~C (A, C not matching) give one set of 3."""
        rule = self._rule([('name', 'partial')], name='T002 linked')
        a = self.Partner.create({'name': 'Marblex'})
        b = self.Partner.create({'name': 'Marblex Delta'})
        c = self.Partner.create({'name': 'Delta'})
        sets = self._open_sets(rule)
        self.assertEqual(len(sets), 1)
        self.assertEqual(self._members(sets), {a.id, b.id, c.id})

    def test_each_rule_judged_on_its_own(self):
        """Requirements: BR-DRD-0003. Two rules give two sets."""
        r1 = self._rule([('email', 'exact')], name='T002 r1')
        r2 = self._rule([('name', 'exact')], name='T002 r2')
        self.Partner.create({'name': 'Twin', 'email': 'twin@rules-t002.com'})
        self.Partner.create({'name': 'Twin', 'email': 'twin@rules-t002.com'})
        self.assertEqual(len(self._open_sets(r1)), 1)
        self.assertEqual(len(self._open_sets(r2)), 1)

    def test_group_has_rule_model_and_counts(self):
        """Requirements: FR-DRD-0004. A set carries its rule, record type and counts."""
        rule = self._rule([('email', 'exact')], name='T002 meta')
        self.Partner.create({'name': 'MetaOne', 'email': 'm@meta-t002.com'})
        self.Partner.create({'name': 'MetaTwo', 'email': 'm@meta-t002.com'})
        group = self._open_sets(rule)
        self.assertEqual(group.model_name, 'res.partner')
        self.assertEqual(group.record_count, 2)
        self.assertIn('MetaOne', group.record_names)
        self.assertIn('MetaTwo', group.record_names)
        self.assertEqual(rule.open_group_count, 1)

    def test_unlink_removes_lines(self):
        """Requirements: FR-DRD-0004. A deleted record leaves no line behind."""
        self._rule([('email', 'exact')], name='T002 unlink')
        a = self.Partner.create({'name': 'UA', 'email': 'u@unlink-t002.com'})
        self.Partner.create({'name': 'UB', 'email': 'u@unlink-t002.com'})
        a_id = a.id
        a.sudo().unlink()
        lines = self.env['duplicate.group.line'].sudo().search(
            [('res_model', '=', 'res.partner'), ('res_id', '=', a_id)])
        self.assertFalse(lines)

    def test_no_field_added_to_partner_or_product(self):
        """Requirements: FR-DRD-0003. The check is behaviour only (mixin, no field)."""
        self.assertIn('duplicate.check.mixin', self.env)
        for model in ('res.partner', 'product.template'):
            for name, field in self.env[model]._fields.items():
                self.assertNotIn(
                    'new_duplicate_record_detector', getattr(field, '_modules', ()),
                    f"{model}.{name} must not be added by the module")
