from unittest.mock import patch

from odoo.tests.common import TransactionCase, new_test_user


class TestHistoryTabPaging(TransactionCase):

    def test_first_page_is_limited_in_the_query(self):
        """Requirements: NFR-PERF-001. The 80 newest entries are fetched, not all then cut."""
        self.env['record.change.model'].sudo().search(
            [('model_id.model', '=', 'res.partner')]).write({'is_followed': True})
        partner = self.env['res.partner'].create({'name': 'Busy'})
        self.env['record.change.entry'].sudo().create([{
            'res_model': 'res.partner', 'res_id': partner.id,
            'field_name': 'phone', 'field_label': 'Phone', 'old_value': str(i),
            'new_value': str(i + 1), 'change_date': '2026-01-01 00:00:00',
        } for i in range(120)])
        user = new_test_user(self.env, login='rct_page_u', groups='base.group_user,base.group_system')
        real = self.env.registry['record.change.entry'].search
        limits = []

        def spy(model, domain, *args, **kwargs):
            if ('res_model', '=', 'res.partner') in domain:
                limits.append(kwargs.get('limit'))
            return real(model, domain, *args, **kwargs)

        with patch.object(self.env.registry['record.change.entry'], 'search', spy):
            entries = partner.with_user(user).change_entry_ids
        self.assertEqual(len(entries), 80)
        self.assertTrue(limits and all(limit == 80 for limit in limits), limits)
