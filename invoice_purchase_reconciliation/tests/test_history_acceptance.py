from odoo.api import SUPERUSER_ID
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestHistoryAcceptance(TransactionCase):
    """Acceptance tests of T-003 (append-only reconciliation history).

    Requirements: FR-IPR-0020, FR-IPR-0021, FR-IPR-0022, NFR-IPR-0002.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company_a = cls.env['res.company'].create({'name': 'IPR Hist A'})
        cls.company_b = cls.env['res.company'].create({'name': 'IPR Hist B'})
        cls.accountant = cls._user('ipr_h_acc', 'account.group_account_invoice', cls.company_a)
        cls.admin = cls._user('ipr_h_adm', 'account.group_account_manager', cls.company_a)
        cls.acme = cls.env['res.partner'].create({'name': 'Acme'})
        cls.journals = {}
        for company in (cls.company_a, cls.company_b):
            cls.journals[company.id] = cls.env['account.journal'].create({
                'name': 'Purchases', 'type': 'purchase', 'code': 'IPRH',
                'company_id': company.id})

    @classmethod
    def _user(cls, login, group_xmlid, company):
        return cls.env['res.users'].with_context(no_reset_password=True).create({
            'name': login, 'login': login,
            'company_id': company.id, 'company_ids': [(6, 0, company.ids)],
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id, cls.env.ref(group_xmlid).id])],
        })

    def _bill(self, company, ref):
        return self.env['account.move'].with_company(company).sudo().create({
            'move_type': 'in_invoice', 'partner_id': self.acme.id, 'ref': ref,
            'invoice_date': '2026-01-15', 'company_id': company.id,
            'journal_id': self.journals[company.id].id,
        })

    def _entries(self, bill):
        return self.env['ipr.history'].sudo().search([('move_id', '=', bill.id)])

    def test_write_refused(self):
        """SC-IPR-0021-1: editing a history entry is refused for everyone, superuser included."""
        bill = self._bill(self.company_a, 'INV-1')
        bill._ipr_add_history('check', 'matched', comment='first')
        entry = self._entries(bill)
        self.assertEqual(len(entry), 1)
        for label, rec in (
            ('admin', entry.with_user(self.admin)),
            ('sudo', entry.sudo()),
            ('superuser', entry.with_user(SUPERUSER_ID)),
        ):
            with self.subTest(who=label):
                with self.assertRaisesRegex(UserError, 'cannot be changed or deleted'):
                    rec.write({'comment': 'edited'})
        self.assertEqual(entry.comment, 'first')

    def test_unlink_refused(self):
        """SC-IPR-0021-1: deleting a history entry is refused and the entry stays."""
        bill = self._bill(self.company_a, 'INV-2')
        bill._ipr_add_history('check', 'matched')
        entry = self._entries(bill)
        for label, rec in (
            ('admin', entry.with_user(self.admin)),
            ('sudo', entry.sudo()),
            ('superuser', entry.with_user(SUPERUSER_ID)),
        ):
            with self.subTest(who=label):
                with self.assertRaisesRegex(UserError, 'cannot be changed or deleted'):
                    rec.unlink()
        self.assertTrue(entry.exists())

    def test_delete_draft_bill_keeps_history(self):
        """SC-IPR-0022-1: deleting a draft bill with two entries keeps them and adds a delete entry.

        The Rejected status of the scenario's bill is set by a later task;
        here the bill is a draft with two entries.
        """
        bill = self._bill(self.company_a, 'INV-778')
        bill._ipr_add_history('check', 'waiting')
        bill._ipr_add_history('reject', 'rejected', comment='wrong price')
        ids = self._entries(bill).ids
        self.assertEqual(len(ids), 2)
        bill.with_user(self.accountant).with_context(allowed_company_ids=self.company_a.ids).unlink()
        self.env.invalidate_all()
        kept = self.env['ipr.history'].sudo().browse(ids)
        self.assertEqual(len(kept.exists()), 2)
        for entry in kept:
            self.assertEqual(entry.partner_name, 'Acme')
            self.assertEqual(entry.bill_reference, 'INV-778')
            self.assertFalse(entry.move_id)
        all_entries = self.env['ipr.history'].sudo().search([('bill_reference', '=', 'INV-778')])
        self.assertEqual(len(all_entries), 3)
        deleted = all_entries - kept
        self.assertEqual(deleted.event, 'delete')
        self.assertEqual(deleted.user_id, self.accountant)
        self.assertEqual(deleted.comment, 'Bill deleted')

    def test_company_isolation_and_kept_entries(self):
        """NFR-IPR-0002: entries of another company are invisible; kept entries of deleted bills only for the administrator."""
        bill_a = self._bill(self.company_a, 'INV-A')
        bill_b = self._bill(self.company_b, 'INV-B')
        bill_a._ipr_add_history('check', 'matched')
        bill_b._ipr_add_history('check', 'matched')
        ctx = {'allowed_company_ids': self.company_a.ids}
        hist = self.env['ipr.history']
        seen = hist.with_user(self.accountant).with_context(**ctx).search([])
        self.assertIn('INV-A', seen.mapped('bill_reference'))
        self.assertNotIn('INV-B', seen.mapped('bill_reference'))
        bill_a.unlink()
        self.env.invalidate_all()
        acc = hist.with_user(self.accountant).with_context(**ctx).search([('bill_reference', '=', 'INV-A')])
        adm = hist.with_user(self.admin).with_context(**ctx).search([('bill_reference', '=', 'INV-A')])
        self.assertFalse(acc)
        self.assertEqual(len(adm), 2)

    def test_exceptions_rendered_in_reader_language(self):
        """FR-IPR-0020: exceptions are stored as data and rendered as translated text."""
        bill = self._bill(self.company_a, 'INV-R')
        entry = self.env['ipr.history'].sudo().create({
            'move_id': bill.id, 'company_id': self.company_a.id, 'user_id': self.admin.id,
            'event': 'check', 'state_after': 'waiting',
            'exceptions_data': [{'kind': 'price'}], 'exception_count': 1,
        })
        self.assertEqual(entry.exception_count, 1)
        self.assertTrue(entry.exceptions_display)
        self.assertIn('price', entry.exceptions_display.lower())

    def test_history_list_and_menu(self):
        """UX-IPR-004: the history list has no create, edit or delete and a menu opens it."""
        view = self.env['ir.ui.view'].sudo().search(
            [('model', '=', 'ipr.history'), ('type', '=', 'list')], limit=1)
        self.assertTrue(view)
        for attr in ('create="0"', 'edit="0"', 'delete="0"'):
            self.assertIn(attr, view.arch_db)
        menus = self.env['ir.ui.menu'].sudo().search([('name', '=', 'Reconciliation History')])
        self.assertTrue(menus)
