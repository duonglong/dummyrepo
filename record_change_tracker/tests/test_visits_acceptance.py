"""Acceptance tests of T-004: per-user last visit and the New highlight.

Written from the spec only (BR-RCT-005, FR-RCT-004, FR-RCT-005, FR-RCT-006, NFR-USE-001).
Opening a record's form is simulated by reading it through web_read with the history tab's
field, as the form view does.
"""
from freezegun import freeze_time

from odoo.tests.common import TransactionCase, new_test_user


class TestVisitsAcceptance(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        groups = ('base.group_user,base.group_system,base.group_partner_manager,'
                  'sales_team.group_sale_salesman_all_leads,purchase.group_purchase_user')
        cls.anna = new_test_user(cls.env, login='rct_v_anna', name='Anna V', groups=groups)
        cls.marc = new_test_user(cls.env, login='rct_v_marc', name='Marc V', groups=groups)
        cls.plain = new_test_user(cls.env, login='rct_v_plain', name='Plain V', groups='base.group_user')
        for model in ('res.partner', 'sale.order', 'product.template'):
            cls.env['record.change.model'].sudo().search(
                [('model_id.model', '=', model)]).write({'is_followed': True})

    def _entry(self, record, date, author, fname='payment_term_id'):
        return self.env['record.change.entry'].sudo().create({
            'res_model': record._name, 'res_id': record.id,
            'field_name': fname, 'field_label': fname,
            'old_value': 'a', 'new_value': 'b',
            'author_id': author.id, 'change_date': date,
        })

    def _open(self, record, user, when):
        """Open the form of `record` as `user` at `when`; return {entry id: is_new}."""
        with freeze_time(when):
            self.env.flush_all()
            self.env.invalidate_all()
            res = record.with_user(user).web_read({
                'display_name': {},
                'change_tracking_followed': {},
                'change_entry_ids': {'fields': {'is_new': {}, 'field_name': {}}},
            })
        return {line['id']: line['is_new'] for line in res[0]['change_entry_ids']}

    def _visit(self, record, user):
        self.env.flush_all()
        return self.env['record.change.visit'].sudo().search([
            ('user_id', '=', user.id), ('res_model', '=', record._name), ('res_id', '=', record.id)])

    def _order(self):
        return self.env['sale.order'].create({'partner_id': self.env['res.partner'].create({'name': 'C'}).id})

    # ---- FR-RCT-004
    def test_004_1_last_visit_updated(self):
        """Requirements: FR-RCT-004, BR-RCT-005. Scenario SC-RCT-004-1."""
        partner = self.env['res.partner'].create({'name': 'Azure Interior'})
        self._open(partner, self.anna, '2026-03-01 09:00:00')
        self.assertEqual(str(self._visit(partner, self.anna).last_visit), '2026-03-01 09:00:00')
        self._open(partner, self.anna, '2026-03-05 10:00:00')
        visit = self._visit(partner, self.anna)
        self.assertEqual(len(visit), 1)
        self.assertEqual(str(visit.last_visit), '2026-03-05 10:00:00')
        self.assertEqual(str(visit.previous_visit), '2026-03-01 09:00:00')

    def test_004_2_other_users_visit_untouched(self):
        """Requirements: FR-RCT-004. Scenario SC-RCT-004-2."""
        partner = self.env['res.partner'].create({'name': 'Azure Interior'})
        self._open(partner, self.anna, '2026-03-01 08:00:00')
        self._open(partner, self.marc, '2026-03-01 09:00:00')
        self._open(partner, self.anna, '2026-03-05 10:00:00')
        self.assertEqual(str(self._visit(partner, self.marc).last_visit), '2026-03-01 09:00:00')
        self.assertEqual(str(self._visit(partner, self.anna).last_visit), '2026-03-05 10:00:00')

    def test_004_unfollowed_type_stores_no_visit(self):
        """Requirements: FR-RCT-004. Only records of a followed record type store a visit."""
        self.env['record.change.model'].sudo().search(
            [('model_id.model', '=', 'purchase.order')]).write({'is_followed': False})
        order = self.env['purchase.order'].create({
            'partner_id': self.env['res.partner'].create({'name': 'S'}).id})
        with freeze_time('2026-03-01 09:00:00'):
            order.with_user(self.anna).web_read({'display_name': {}})
        self.assertFalse(self._visit(order, self.anna))

    # ---- FR-RCT-005
    def test_005_1_other_users_change_after_visit_is_new(self):
        """Requirements: FR-RCT-005, NFR-USE-001. Scenario SC-RCT-005-1."""
        order = self._order()
        self._open(order, self.anna, '2026-03-02 09:00:00')  # Monday
        tuesday = self._entry(order, '2026-03-03 10:00:00', self.marc)
        marks = self._open(order, self.anna, '2026-03-04 09:00:00')  # Wednesday
        self.assertTrue(marks[tuesday.id])

    def test_005_2_entry_before_visit_not_new(self):
        """Requirements: FR-RCT-005. Scenario SC-RCT-005-2."""
        order = self._order()
        sunday = self._entry(order, '2026-03-01 10:00:00', self.marc)
        self._open(order, self.anna, '2026-03-02 09:00:00')
        marks = self._open(order, self.anna, '2026-03-04 09:00:00')
        self.assertFalse(marks[sunday.id])

    def test_005_3_own_change_not_new(self):
        """Requirements: FR-RCT-005. Scenario SC-RCT-005-3."""
        order = self._order()
        self._open(order, self.anna, '2026-03-02 09:00:00')
        own = self._entry(order, '2026-03-03 10:00:00', self.anna)
        marks = self._open(order, self.anna, '2026-03-04 09:00:00')
        self.assertFalse(marks[own.id])

    def test_005_marks_compare_with_previous_opening(self):
        """Requirements: FR-RCT-004, FR-RCT-005. A change is New on the opening that follows it only."""
        order = self._order()
        self._open(order, self.anna, '2026-03-02 09:00:00')
        change = self._entry(order, '2026-03-03 10:00:00', self.marc)
        self.assertTrue(self._open(order, self.anna, '2026-03-04 09:00:00')[change.id])
        self.assertFalse(self._open(order, self.anna, '2026-03-05 09:00:00')[change.id])

    def test_005_new_is_per_user(self):
        """Requirements: FR-RCT-005. The mark depends on each user's own previous opening."""
        order = self._order()
        self._open(order, self.anna, '2026-03-02 09:00:00')
        self._open(order, self.marc, '2026-03-04 00:00:00')
        change = self._entry(order, '2026-03-03 10:00:00', self.plain.sudo().copy({'login': 'rct_v_x'}))
        self.assertTrue(self._open(order, self.anna, '2026-03-05 09:00:00')[change.id])
        self.assertFalse(self._open(order, self.marc, '2026-03-05 09:00:00')[change.id])

    # ---- FR-RCT-006
    def test_006_1_first_visit_no_new(self):
        """Requirements: FR-RCT-006. Scenario SC-RCT-006-1."""
        product = self.env['product.template'].create({'name': 'Desk'})
        entries = [self._entry(product, '2026-03-0%d 10:00:00' % d, self.marc, 'name') for d in range(1, 6)]
        self.assertFalse(self._visit(product, self.anna))
        marks = self._open(product, self.anna, '2026-03-10 09:00:00')
        self.assertEqual(len(marks), 5)
        for entry in entries:
            self.assertFalse(marks[entry.id])
        visit = self._visit(product, self.anna)
        self.assertEqual(str(visit.last_visit), '2026-03-10 09:00:00')
        self.assertFalse(visit.previous_visit)

    # ---- NFR-USE-001
    def test_usability_new_word_in_view(self):
        """Requirements: NFR-USE-001. The history tab shows the word New for marked lines."""
        arch = self.env['sale.order'].with_user(self.anna).get_view(view_type='form')['arch']
        self.assertIn('is_new', arch)
        self.assertIn('New', arch)

    # ---- NFR-SEC-001 (visit data)
    def test_sec_visit_data_follows_record_access(self):
        """Requirements: NFR-SEC-001. A user who cannot read a record sees none of its visit data."""
        order = self._order()
        self._open(order, self.anna, '2026-03-02 09:00:00')
        visit = self._visit(order, self.anna)
        self.assertTrue(visit)
        Visit = self.env['record.change.visit']
        self.assertFalse(Visit.with_user(self.plain).search([('id', '=', visit.id)]))
