from datetime import datetime, timedelta
from unittest.mock import patch

from odoo import fields
from odoo.tests.common import TransactionCase, new_test_user


class TestHistoryTabPaging(TransactionCase):

    def test_first_page_is_limited_in_the_query(self):
        """Requirements: NFR-PERF-001. The 80 newest entries are fetched, not all then cut."""
        self.env['record.change.model'].sudo().search(
            [('model_id.model', '=', 'res.partner')]).write({'is_followed': True})
        partner = self.env['res.partner'].create({'name': 'Busy'})
        base = datetime(2026, 1, 1)
        self.env['record.change.entry'].sudo().create([{
            'res_model': 'res.partner', 'res_id': partner.id,
            'field_name': 'phone', 'field_label': 'Phone', 'old_value': str(i),
            'new_value': str(i + 1),
            'change_date': fields.Datetime.to_string(base + timedelta(minutes=i)),
        } for i in range(1000)])
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
        # the 80 newest: old values 999 down to 920
        self.assertEqual(entries.mapped('old_value'), [str(i) for i in range(999, 919, -1)])
        self.assertTrue(limits and all(limit == 80 for limit in limits), limits)
