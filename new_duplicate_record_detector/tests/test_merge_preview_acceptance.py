from odoo import Command
from odoo.exceptions import AccessError, UserError
from odoo.tests.common import TransactionCase


class TestMergePreviewAcceptance(TransactionCase):
    """Acceptance tests of T-004 (merge preview dialog).

    Requirements: BR-DRD-0006, FR-DRD-0009, FR-DRD-0017.

    The wizard is started the Odoo way: ``active_model`` and ``active_ids`` in the context
    (a list selection), or ``active_model='duplicate.group'`` with the set in ``active_id``.
    Bank accounts stand for "linked documents" (sale and account are not dependencies).
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        base = cls.env.ref('base.group_user') | cls.env.ref('base.group_partner_manager')
        mgr_group = cls.env.ref('new_duplicate_record_detector.group_duplicate_manager')
        cls.manager = cls.env['res.users'].create({
            'name': 'Dup Mgr T004', 'login': 'dup_mgr_t004',
            'group_ids': [Command.set((base | mgr_group).ids)],
        })
        cls.sales = cls.env['res.users'].create({
            'name': 'Sales T004', 'login': 'sales_t004',
            'group_ids': [Command.set(base.ids)],
        })
        cls.admin = cls.env['res.users'].create({
            'name': 'Admin T004', 'login': 'admin_t004',
            'group_ids': [Command.set((cls.env.ref('base.group_user')
                                       | cls.env.ref('base.group_system')).ids)],
        })
        cls.P = cls.env['res.partner']
        cls.bank_label = {cls.env['ir.model']._get('res.partner.bank').name,
                          cls.env['res.partner.bank']._description}

    # helpers
    def _banks(self, partner, count, tag='T004'):
        for i in range(count):
            self.env['res.partner.bank'].create({
                'account_number': '%s-%s-%s' % (tag, partner.id, i), 'partner_id': partner.id})

    def _pair(self):
        self.acme = self.P.create({'name': 'Acme', 'email': 'info@acme.com'})
        self.acme_ltd = self.P.create({
            'name': 'ACME Ltd', 'email': 'sales@acme.com', 'phone': '555-0100'})
        self._banks(self.acme, 2)
        self._banks(self.acme_ltd, 3)
        return self.acme, self.acme_ltd

    def _start(self, ids, user=None, model='res.partner'):
        wizard = self.env['duplicate.merge.wizard'].with_user(user or self.manager)
        return wizard.with_context(active_model=model, active_ids=list(ids)).create({})

    def _start_group(self, group, user=None):
        wizard = self.env['duplicate.merge.wizard'].with_user(user or self.manager)
        return wizard.with_context(
            active_model='duplicate.group', active_id=group.id,
            active_ids=group.ids).create({})

    def _set(self, partners):
        rule = self.env['duplicate.rule'].sudo().create({
            'name': 'T004 rule',
            'model_id': self.env['ir.model']._get('res.partner').id,
            'condition_ids': [Command.create({
                'field_id': self.env['ir.model.fields']._get('res.partner', 'vat').id,
                'match_type': 'exact'})],
        })
        return self.env['duplicate.group'].sudo().create({
            'rule_id': rule.id, 'model_name': 'res.partner',
            'line_ids': [Command.create({
                'res_model': 'res.partner', 'res_id': p.id, 'name': p.display_name})
                for p in partners],
        })

    def _link(self, wizard):
        lines = wizard.link_line_ids.filtered(lambda l: l.model_label in self.bank_label)
        self.assertEqual(len(lines), 1, "one link line for bank accounts expected: %s"
                         % wizard.link_line_ids.mapped('model_label'))
        return lines

    def _value(self, wizard, field):
        lines = wizard.value_line_ids.filtered(lambda l: l.field_name == field)
        self.assertEqual(len(lines), 1, "one value line for %s expected" % field)
        return lines

    # SC-DRD-0009-1
    def test_sc_0009_1_preview_shows_kept_values_links_archive(self):
        """Requirements: FR-DRD-0009. Scenario SC-DRD-0009-1."""
        acme, ltd = self._pair()
        group = self._set(acme | ltd)
        wizard = self._start_group(group)
        self.assertEqual(wizard.kept_res_id, acme.id)
        phone = self._value(wizard, 'phone')
        self.assertEqual(phone.kept_value, '555-0100')
        self.assertEqual(phone.source_name, 'ACME Ltd')
        self.assertTrue(phone.is_filled)
        email = self._value(wizard, 'email')
        self.assertEqual(email.kept_value, 'info@acme.com')
        self.assertEqual(email.source_name, 'Acme')
        self.assertFalse(email.is_filled)
        self.assertEqual(self._link(wizard).count, 3)
        self.assertEqual(wizard.archive_names, 'ACME Ltd')

    def test_sc_0009_1_start_from_a_list_selection(self):
        """Requirements: FR-DRD-0009. Scenario SC-DRD-0009-1 (list selection start)."""
        acme, ltd = self._pair()
        wizard = self._start([acme.id, ltd.id])
        self.assertEqual(wizard.res_model, 'res.partner')
        self.assertEqual(wizard.kept_res_id, acme.id)
        self.assertEqual(wizard.archive_names, 'ACME Ltd')
        self.assertEqual(set(wizard.record_line_ids.mapped('res_id')), {acme.id, ltd.id})
        self.assertTrue(all(wizard.record_line_ids.mapped('include')))
        kept = wizard.record_line_ids.filtered('is_kept')
        self.assertEqual(kept.res_id, acme.id)

    def test_sc_0009_1_kept_is_the_oldest_by_creation_date(self):
        """Requirements: FR-DRD-0009. The kept record is the oldest, not the first listed."""
        ltd = self.P.create({'name': 'ACME Ltd'})
        acme = self.P.create({'name': 'Acme'})
        self.env.cr.execute(
            "UPDATE res_partner SET create_date = create_date - interval '1 day' WHERE id = %s",
            [acme.id])
        self.env.invalidate_all()
        wizard = self._start([ltd.id, acme.id])
        self.assertEqual(wizard.kept_res_id, acme.id)
        self.assertEqual(wizard.archive_names, 'ACME Ltd')

    # SC-DRD-0009-2
    def test_sc_0009_2_choose_another_kept_record(self):
        """Requirements: FR-DRD-0009. Scenario SC-DRD-0009-2."""
        acme, ltd = self._pair()
        wizard = self._start([acme.id, ltd.id])
        wizard.kept_res_id = ltd.id
        self.assertEqual(wizard.archive_names, 'Acme')
        self.assertEqual(self._link(wizard).count, 2)
        phone = self._value(wizard, 'phone')
        self.assertEqual(phone.kept_value, '555-0100')
        self.assertFalse(phone.is_filled)
        self.assertEqual(wizard.record_line_ids.filtered('is_kept').res_id, ltd.id)

    def test_excluded_record_is_left_out_of_the_preview(self):
        """Requirements: FR-DRD-0009. A record left out is neither archived nor counted."""
        acme, ltd = self._pair()
        third = self.P.create({'name': 'Acme Trading'})
        self._banks(third, 4, tag='T004c')
        wizard = self._start([acme.id, ltd.id, third.id])
        self.assertEqual(self._link(wizard).count, 7)
        wizard.record_line_ids.filtered(lambda l: l.res_id == third.id).include = False
        self.assertEqual(wizard.archive_names, 'ACME Ltd')
        self.assertEqual(self._link(wizard).count, 3)

    # SC-DRD-0009-3
    def test_sc_0009_3_closing_without_confirming_changes_nothing(self):
        """Requirements: FR-DRD-0009. Scenario SC-DRD-0009-3."""
        acme, ltd = self._pair()
        wizard = self._start([acme.id, ltd.id])
        wizard.kept_res_id = ltd.id
        wizard.unlink()
        self.env.flush_all()
        self.env.invalidate_all()
        for partner in (acme, ltd):
            self.assertTrue(partner.active)
        self.assertFalse(acme.phone)
        self.assertEqual(ltd.phone, '555-0100')
        self.assertEqual(len(acme.bank_ids), 2)
        self.assertEqual(len(ltd.bank_ids), 3)

    def test_preview_alone_changes_no_record(self):
        """Requirements: FR-DRD-0009. Nothing changes until the merge is confirmed."""
        acme, ltd = self._pair()
        self._start([acme.id, ltd.id])
        self.env.flush_all()
        self.env.invalidate_all()
        self.assertTrue(acme.active and ltd.active)
        self.assertFalse(acme.phone)
        self.assertEqual(len(ltd.bank_ids), 3)

    # SC-DRD-0009-4 and BR-DRD-0006 boundaries
    def test_sc_0009_4_eleven_records_refused(self):
        """Requirements: BR-DRD-0006, FR-DRD-0009. Scenario SC-DRD-0009-4 (just above 10)."""
        partners = self.P.create([{'name': 'T004 many %s' % i} for i in range(11)])
        with self.assertRaisesRegex(UserError, '2 to 10'):
            self._start(partners.ids)

    def test_br_0006_ten_records_accepted(self):
        """Requirements: BR-DRD-0006. At the upper limit of 10."""
        partners = self.P.create([{'name': 'T004 ten %s' % i} for i in range(10)])
        wizard = self._start(partners.ids)
        self.assertEqual(len(wizard.record_line_ids), 10)

    def test_br_0006_two_records_accepted(self):
        """Requirements: BR-DRD-0006. At the lower limit of 2."""
        partners = self.P.create([{'name': 'T004 two %s' % i} for i in range(2)])
        wizard = self._start(partners.ids)
        self.assertEqual(len(wizard.record_line_ids), 2)

    def test_br_0006_one_record_refused(self):
        """Requirements: BR-DRD-0006. Just below the lower limit."""
        partner = self.P.create({'name': 'T004 alone'})
        with self.assertRaisesRegex(UserError, '2 to 10'):
            self._start(partner.ids)

    # SC-DRD-0017-1
    def test_sc_0017_1_non_manager_refused_from_a_selection(self):
        """Requirements: FR-DRD-0017. Scenario SC-DRD-0017-1."""
        acme, ltd = self._pair()
        with self.assertRaises(AccessError):
            self._start([acme.id, ltd.id], user=self.sales)
        self.assertTrue(acme.active and ltd.active)

    def test_sc_0017_1_non_manager_refused_from_a_set(self):
        """Requirements: FR-DRD-0017. Scenario SC-DRD-0017-1 (from a set)."""
        acme, ltd = self._pair()
        group = self._set(acme | ltd)
        with self.assertRaises(AccessError):
            self._start_group(group, user=self.sales)
        self.assertEqual(group.state, 'open')

    # SC-DRD-0017-2
    def test_sc_0017_2_administrator_without_manager_group_can_preview(self):
        """Requirements: FR-DRD-0017. Scenario SC-DRD-0017-2."""
        acme, ltd = self._pair()
        wizard = self._start([acme.id, ltd.id], user=self.admin)
        self.assertEqual(wizard.kept_res_id, acme.id)
        self.assertEqual(wizard.archive_names, 'ACME Ltd')
