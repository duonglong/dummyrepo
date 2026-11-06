from freezegun import freeze_time

from odoo.tests.common import TransactionCase, new_test_user

SPEC = {
    'display_name': {},
    'change_entry_ids': {'fields': {'is_new': {}}},
}


class TestVisits(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        groups = 'base.group_user,base.group_system,base.group_partner_manager'
        cls.anna = new_test_user(cls.env, login='rct_vv_anna', name='Anna VV', groups=groups)
        cls.marc = new_test_user(cls.env, login='rct_vv_marc', name='Marc VV', groups=groups)
        cls.partner = cls.env['res.partner'].create({'name': 'Azure VV'})
        cls.env['record.change.model'].sudo().search(
            [('model_id.model', '=', 'res.partner')]).write({'is_followed': True})

    def _entry(self, date):
        return self.env['record.change.entry'].sudo().create({
            'res_model': 'res.partner', 'res_id': self.partner.id,
            'field_name': 'name', 'field_label': 'Name', 'old_value': 'a', 'new_value': 'b',
            'author_id': self.marc.id, 'change_date': date})

    def _open(self, when):
        with freeze_time(when):
            self.env.flush_all()
            self.env.invalidate_all()
            res = self.partner.with_user(self.anna).web_read(SPEC)
        return {line['id']: line['is_new'] for line in res[0]['change_entry_ids']}

    def _visit(self):
        self.env.flush_all()
        return self.env['record.change.visit'].sudo().search([
            ('user_id', '=', self.anna.id), ('res_model', '=', 'res.partner'),
            ('res_id', '=', self.partner.id)])

    def test_save_is_not_a_visit(self):
        """Requirements: FR-RCT-004, BR-RCT-005. Saving the form keeps New marks and visits."""
        self._open('2026-03-01 09:00:00')
        entry = self._entry('2026-03-02 09:00:00')
        self.assertTrue(self._open('2026-03-03 09:00:00')[entry.id])
        with freeze_time('2026-03-04 09:00:00'):
            res = self.partner.with_user(self.anna).web_save({'comment': 'x'}, SPEC)
        self.assertTrue({l['id']: l['is_new'] for l in res[0]['change_entry_ids']}[entry.id])
        self.assertEqual(str(self._visit().last_visit), '2026-03-03 09:00:00')
        with freeze_time('2026-03-04 10:00:00'):
            self.partner.with_user(self.anna).web_save_multi([{'comment': 'y'}], SPEC)
        self.assertEqual(str(self._visit().last_visit), '2026-03-03 09:00:00')

    def test_save_with_next_record_is_a_visit_of_the_next(self):
        """Requirements: FR-RCT-004, BR-RCT-005. Pager after an edit opens the next record."""
        other = self.env['res.partner'].create({'name': 'Other VV'})
        self._open('2026-03-01 09:00:00')
        with freeze_time('2026-03-02 09:00:00'):
            res = self.partner.with_user(self.anna).web_save(
                {'comment': 'x'}, SPEC, next_id=other.id)
        self.assertEqual(res[0]['id'], other.id)
        self.assertEqual(str(self._visit().last_visit), '2026-03-01 09:00:00')
        self.env.flush_all()
        visit = self.env['record.change.visit'].sudo().search([
            ('user_id', '=', self.anna.id), ('res_model', '=', 'res.partner'),
            ('res_id', '=', other.id)])
        self.assertEqual(str(visit.last_visit), '2026-03-02 09:00:00')

    def test_boundary_at_previous_visit(self):
        """Requirements: FR-RCT-005. Only changes strictly after the previous visit are New."""
        self._open('2026-03-01 09:00:00')
        before = self._entry('2026-03-01 08:59:59')
        equal = self._entry('2026-03-01 09:00:00')
        after = self._entry('2026-03-01 09:00:01')
        marks = self._open('2026-03-05 09:00:00')
        self.assertFalse(marks[before.id])
        self.assertFalse(marks[equal.id])
        self.assertTrue(marks[after.id])

    def test_unfollowed_type_stores_no_visit_with_history(self):
        """Requirements: FR-RCT-004, FR-RCT-015. Unfollowed type: opening stores no visit."""
        self.env['record.change.model'].sudo().search(
            [('model_id.model', '=', 'res.partner')]).write({'is_followed': False})
        self._open('2026-03-01 09:00:00')
        self.assertFalse(self._visit())
