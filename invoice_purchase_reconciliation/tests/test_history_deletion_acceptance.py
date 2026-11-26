from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestHistoryDeletionAcceptance(TransactionCase):
    """Acceptance tests of T-003: the deletion trail of any vendor bill with history.

    Requirements: FR-IPR-0022, US-IPR-006.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env['res.company'].create({'name': 'IPR Del A'})
        cls.accountant = cls.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'ipr_d_acc', 'login': 'ipr_d_acc',
            'company_id': cls.company.id, 'company_ids': [(6, 0, cls.company.ids)],
            'group_ids': [(6, 0, [
                cls.env.ref('base.group_user').id,
                cls.env.ref('account.group_account_invoice').id])],
        })
        cls.acme = cls.env['res.partner'].create({'name': 'Acme'})
        cls.journal = cls.env['account.journal'].create({
            'name': 'Purchases', 'type': 'purchase', 'code': 'IPRD',
            'company_id': cls.company.id})

    def _bill(self, ref):
        return self.env['account.move'].with_company(self.company).sudo().create({
            'move_type': 'in_invoice', 'partner_id': self.acme.id, 'ref': ref,
            'invoice_date': '2026-01-15', 'company_id': self.company.id,
            'journal_id': self.journal.id,
        })

    def _delete_as_accountant(self, bills):
        bills.with_user(self.accountant).with_context(
            allowed_company_ids=self.company.ids).unlink()
        self.env.invalidate_all()

    def test_delete_cancelled_bill_keeps_history(self):
        """SC-IPR-001: a cancelled never-posted bill with two entries is deleted: entries kept, a third records the deletion."""
        bill = self._bill('INV-778')
        bill._ipr_add_history('check', 'waiting')
        bill._ipr_add_history('reject', 'rejected', comment='wrong price')
        bill.button_cancel()
        self.assertEqual(bill.state, 'cancel')
        kept = self.env['ipr.history'].sudo().search([('move_id', '=', bill.id)])
        self.assertEqual(len(kept), 2)
        ids = kept.ids
        self._delete_as_accountant(bill)
        kept = self.env['ipr.history'].sudo().browse(ids)
        self.assertEqual(len(kept.exists()), 2)
        for entry in kept:
            self.assertEqual(entry.partner_name, 'Acme')
            self.assertEqual(entry.bill_reference, 'INV-778')
        entries = self.env['ipr.history'].sudo().search([('bill_reference', '=', 'INV-778')])
        self.assertEqual(len(entries), 3)
        deleted = entries - kept
        self.assertEqual(deleted.event, 'delete')
        self.assertEqual(deleted.user_id, self.accountant)
        self.assertEqual(deleted.partner_name, 'Acme')

    def test_delete_several_bills_one_entry_each(self):
        """FR-IPR-0022: deleting several bills with history adds one deletion entry per bill."""
        bill_1 = self._bill('INV-M1')
        bill_2 = self._bill('INV-M2')
        for bill in (bill_1, bill_2):
            bill._ipr_add_history('check', 'matched')
        self._delete_as_accountant(bill_1 | bill_2)
        for ref in ('INV-M1', 'INV-M2'):
            entries = self.env['ipr.history'].sudo().search([('bill_reference', '=', ref)])
            self.assertEqual(len(entries), 2, ref)
            self.assertEqual(entries.filtered(lambda e: e.event == 'delete').user_id, self.accountant)

    def test_delete_bill_without_history_adds_no_entry(self):
        """FR-IPR-0022: a bill with no history leaves no entry when deleted."""
        bill = self._bill('INV-NONE')
        self._delete_as_accountant(bill)
        self.assertFalse(self.env['ipr.history'].sudo().search([('bill_reference', '=', 'INV-NONE')]))
