from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestHistory(TransactionCase):
    """Unit tests of the reconciliation history helper and its rendering.

    Requirements: FR-IPR-0020, FR-IPR-0021, FR-IPR-0022, NFR-IPR-0002
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env['res.company'].create({'name': 'IPR History Co'})
        cls.partner = cls.env['res.partner'].create({'name': 'Acme'})
        cls.journal = cls.env['account.journal'].create({
            'name': 'Purchases', 'type': 'purchase', 'code': 'IPRX',
            'company_id': cls.company.id})
        cls.bill = cls.env['account.move'].with_company(cls.company).create({
            'move_type': 'in_invoice', 'partner_id': cls.partner.id, 'ref': 'INV-9',
            'invoice_date': '2026-01-15', 'journal_id': cls.journal.id,
            'company_id': cls.company.id})

    def test_entry_copies_bill_data_and_tolerances(self):
        """FR-IPR-0020: an entry keeps who, the bill data, the tolerances and the exceptions."""
        tolerances = self.env['ipr.tolerance']._ipr_for_company(self.company)
        tolerances['price_percent'] = 5.0
        entry = self.bill._ipr_add_history(
            'check', 'waiting', comment='c', tolerances=tolerances,
            exceptions=[{'kind': 'price', 'product': 'Desk'}])
        self.assertEqual(entry.user_id, self.env.user)
        self.assertEqual(entry.partner_name, 'Acme')
        self.assertEqual(entry.bill_reference, 'INV-9')
        self.assertEqual(entry.company_id, self.company)
        self.assertEqual(entry.price_tolerance_percent, 5.0)
        self.assertIn('5.00 %', entry.tolerances_display)
        self.assertEqual(entry.exception_count, 1)
        self.assertIn('Desk', entry.exceptions_display)

    def test_write_refused_message(self):
        """FR-IPR-0021: the refusal names the rule."""
        entry = self.bill._ipr_add_history('check', 'matched')
        with self.assertRaisesRegex(UserError, 'Reconciliation history cannot be changed or deleted.'):
            entry.write({'comment': 'x'})

    def test_bill_without_history_deleted_adds_nothing(self):
        """FR-IPR-0022: a draft bill with no history leaves no entry when deleted."""
        other = self.bill.copy({'ref': 'INV-10'})
        other.unlink()
        self.assertFalse(self.env['ipr.history'].search([('bill_reference', '=', 'INV-10')]))

    def test_delete_entry_keeps_last_status(self):
        """FR-IPR-0022: the delete entry carries the status the bill had."""
        self.bill._ipr_add_history('reject', 'rejected')
        self.bill.unlink()
        deleted = self.env['ipr.history'].search(
            [('bill_reference', '=', 'INV-9'), ('event', '=', 'delete')])
        self.assertEqual(deleted.state_after, 'rejected')

    def test_list_view_has_no_edit_buttons_and_filters(self):
        """UX-IPR-004: no create, edit or delete; filters by event and supplier."""
        arch = self.env['ipr.history'].get_view(view_type='list')['arch']
        for attr in ('create="0"', 'edit="0"', 'delete="0"'):
            self.assertIn(attr, arch)
        search = self.env['ipr.history'].get_view(view_type='search')['arch']
        self.assertIn('filter_check', search)
        self.assertIn('partner_name', search)

    def test_bill_name_is_raw_number_not_display_label(self):
        """FR-IPR-0020: a draft bill has no number; two drafts with one reference stay apart."""
        twin = self.bill.copy({'ref': 'INV-9'})
        first = self.bill._ipr_add_history('check', 'matched')
        second = twin._ipr_add_history('check', 'matched')
        self.assertFalse(first.move_name)
        self.assertFalse(second.move_name)
        groups = self.env['ipr.history']._read_group(
            [('id', 'in', (first | second).ids)], ['move_id'], ['__count'])
        self.assertEqual(len(groups), 2)

    def test_posted_bill_keeps_its_number(self):
        """FR-IPR-0020: a posted bill's entry stores its number."""
        self.bill.name = 'BILL/2026/0001'
        entry = self.bill._ipr_add_history('check', 'matched')
        self.assertEqual(entry.move_name, self.bill.name)

    def test_delete_trail_only_for_draft_vendor_bills(self):
        """FR-IPR-0022: a credit note with history leaves no delete entry."""
        note = self.bill.copy({'move_type': 'in_refund', 'ref': 'CN-1'})
        note._ipr_add_history('check', 'matched')
        note.unlink()
        self.assertFalse(self.env['ipr.history'].search(
            [('bill_reference', '=', 'CN-1'), ('event', '=', 'delete')]))

    def test_delete_several_bills_adds_one_entry_each(self):
        """FR-IPR-0022: deleting two bills at once records both deletions."""
        twin = self.bill.copy({'ref': 'INV-11'})
        self.bill._ipr_add_history('check', 'matched')
        twin._ipr_add_history('reject', 'rejected')
        (self.bill | twin).unlink()
        deleted = self.env['ipr.history'].search([('event', '=', 'delete')])
        self.assertEqual(
            sorted(deleted.mapped('state_after')), ['matched', 'rejected'])
