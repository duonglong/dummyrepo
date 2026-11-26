from odoo import Command, fields
from odoo.tests import Form, tagged

from .common import IprCommon


@tagged('post_install', '-at_install')
class TestResetAcceptance(IprCommon):
    """Acceptance tests of T-008: a checked draft bill that changes goes back to Not checked.

    Requirements: FR-IPR-0018, FR-IPR-0020.
    Changes are made by writing the bill and its lines; the form save is tested with the
    Form helper on a bill left unchanged.
    """

    def _refresh(self):
        self.env.flush_all()
        self.env.invalidate_all()

    def _set_creator(self, bill, user):
        self.env.flush_all()
        self.env.cr.execute("UPDATE account_move SET create_uid = %s WHERE id = %s", (user.id, bill.id))
        bill.invalidate_recordset(['create_uid'])

    def _approved_bill(self, ref):
        """Draft bill 10 x 12.00 against an order at 10.00, checked and approved by a manager."""
        order = self.make_order(10, 10.0, received=10)
        bill = self.make_bill_from_order(order, 10, 12.0, ref=ref)
        self._set_creator(bill, self.accountant)
        bill.with_user(self.accountant).action_ipr_check()
        bill.with_user(self.purchase_manager).action_ipr_approve('Agreed')
        self._refresh()
        self.assertEqual(bill.ipr_state, 'approved')
        return bill

    def _resets(self, bill):
        return bill.sudo().ipr_history_ids.filtered(lambda h: h.event == 'reset')

    def _product_line(self, bill):
        return bill.invoice_line_ids.filtered(lambda line: line.display_type == 'product')

    # SC-IPR-0018-1
    def test_price_change_resets(self):
        """SC-IPR-0018-1: changing a line's price 12.00 -> 15.00 sets Not checked, records the reset, next post re-checks."""
        bill = self._approved_bill('RESET-PRICE')
        checks_before = len(bill.sudo().ipr_history_ids.filtered(lambda h: h.event == 'check'))
        bill.write({'invoice_line_ids': [Command.update(self._product_line(bill).id, {'price_unit': 15.0})]})
        self._refresh()
        self.assertEqual(bill.ipr_state, 'not_checked')
        self.assertFalse(bill.ipr_waiting_since)
        self.assertFalse(bill.ipr_decided_by_id)
        self.assertFalse(bill.ipr_decided_date)
        self.assertFalse(bill.ipr_check_line_ids)
        self.assertFalse(bill.ipr_exception_ids)
        resets = self._resets(bill)
        self.assertEqual(len(resets), 1)
        self.assertEqual(resets.state_after, 'not_checked')
        # the next posting runs the check again: 15.00 against 10.00 is held
        bill.with_user(self.accountant).action_post()
        self._refresh()
        self.assertEqual(bill.state, 'draft')
        self.assertEqual(bill.ipr_state, 'waiting')
        self.assertEqual(
            len(bill.sudo().ipr_history_ids.filtered(lambda h: h.event == 'check')), checks_before + 1)

    def test_multi_field_write_one_entry(self):
        """FR-IPR-0020: one write changing several watched fields adds one reset entry."""
        bill = self._approved_bill('RESET-MULTI')
        bill.write({
            'ref': 'RESET-MULTI-2',
            'invoice_line_ids': [Command.update(self._product_line(bill).id, {'price_unit': 15.0, 'quantity': 5})],
        })
        self._refresh()
        self.assertEqual(bill.ipr_state, 'not_checked')
        self.assertEqual(len(self._resets(bill)), 1)

    # SC-IPR-0018-2
    def test_due_date_keeps_status(self):
        """SC-IPR-0018-2: changing only the due date keeps Approved and adds no reset entry."""
        bill = self._approved_bill('RESET-DUE')
        bill.write({'invoice_date_due': fields.Date.add(fields.Date.today(), days=45)})
        bill.write({'narration': 'A note'})
        self._refresh()
        self.assertEqual(bill.ipr_state, 'approved')
        self.assertFalse(self._resets(bill))

    def test_same_value_write_keeps_status(self):
        """FR-IPR-0018: writing the values the bill already has changes nothing."""
        bill = self._approved_bill('RESET-SAME')
        line = self._product_line(bill)
        bill.write({
            'ref': bill.ref,
            'partner_id': bill.partner_id.id,
            'currency_id': bill.currency_id.id,
            'invoice_line_ids': [Command.update(line.id, {
                'price_unit': line.price_unit, 'quantity': line.quantity,
                'product_id': line.product_id.id, 'discount': line.discount,
                'purchase_line_id': line.purchase_line_id.id,
            })],
        })
        self._refresh()
        self.assertEqual(bill.ipr_state, 'approved')
        self.assertFalse(self._resets(bill))

    def test_form_save_without_change_keeps_status(self):
        """FR-IPR-0018: opening the bill form and saving without a change keeps Approved."""
        bill = self._approved_bill('RESET-FORM')
        with Form(bill) as form:
            form.narration = 'A note'
        self._refresh()
        self.assertEqual(bill.ipr_state, 'approved')
        with Form(bill):
            pass
        self._refresh()
        self.assertEqual(bill.ipr_state, 'approved')
        self.assertFalse(self._resets(bill))

    def test_each_watched_field(self):
        """FR-IPR-0018: supplier, reference, currency, product, quantity, discount, unit and order link each reset."""
        eur = self.env.ref('base.EUR')
        eur.sudo().active = True
        other_product = self.env['product.product'].with_company(self.company_a).create({
            'name': 'Desk', 'is_storable': True, 'type': 'consu', 'standard_price': 20.0,
        })
        other_order = self.make_order(5, 10.0, received=5)
        cases = {
            'partner': lambda bill, line: {'partner_id': self.supplier_beta.id},
            'ref': lambda bill, line: {'ref': 'CHANGED-REF'},
            'currency': lambda bill, line: {'currency_id': eur.id},
            'product': lambda bill, line: {'invoice_line_ids': [
                Command.update(line.id, {'product_id': other_product.id})]},
            'quantity': lambda bill, line: {'invoice_line_ids': [
                Command.update(line.id, {'quantity': 4})]},
            'discount': lambda bill, line: {'invoice_line_ids': [
                Command.update(line.id, {'discount': 10.0})]},
            'uom': lambda bill, line: {'invoice_line_ids': [Command.update(line.id, {
                'product_uom_id': self.env['uom.uom'].search(
                    [('id', '!=', line.product_uom_id.id)], limit=1).id})]},
            'purchase_line': lambda bill, line: {'invoice_line_ids': [
                Command.update(line.id, {'purchase_line_id': other_order.order_line.id})]},
        }
        for name, make_vals in cases.items():
            with self.subTest(field=name):
                bill = self._approved_bill(f'WATCH-{name}')
                bill.write(make_vals(bill, self._product_line(bill)))
                self._refresh()
                self.assertEqual(bill.ipr_state, 'not_checked', name)
                self.assertEqual(len(self._resets(bill)), 1, name)

    def test_add_and_remove_line_resets(self):
        """FR-IPR-0018: adding or removing a product line of a checked draft resets it."""
        added = self._approved_bill('RESET-ADD')
        added.write({'invoice_line_ids': [Command.create({
            'product_id': self.product.id, 'quantity': 1, 'price_unit': 10.0})]})
        self._refresh()
        self.assertEqual(added.ipr_state, 'not_checked')
        self.assertEqual(len(self._resets(added)), 1)

        removed = self._approved_bill('RESET-REMOVE')
        removed.write({'invoice_line_ids': [Command.create({
            'product_id': self.product.id, 'quantity': 1, 'price_unit': 10.0})]})
        removed.write({'ipr_state': 'approved'})  # superuser: back to Approved before the removal
        self._refresh()
        removed.write({'invoice_line_ids': [Command.delete(self._product_line(removed)[-1].id)]})
        self._refresh()
        self.assertEqual(removed.ipr_state, 'not_checked')

    def test_nonproduct_line_ignored(self):
        """FR-IPR-0018: a section or note line added, changed or removed keeps the status."""
        bill = self._approved_bill('RESET-SECTION')
        bill.write({'invoice_line_ids': [Command.create({
            'display_type': 'line_section', 'name': 'Delivery', 'quantity': 0})]})
        self._refresh()
        self.assertEqual(bill.ipr_state, 'approved')
        section = bill.invoice_line_ids.filtered(lambda line: line.display_type == 'line_section')
        bill.write({'invoice_line_ids': [Command.update(section.id, {'name': 'Delivery 2'})]})
        self._refresh()
        self.assertEqual(bill.ipr_state, 'approved')
        bill.write({'invoice_line_ids': [Command.delete(section.id)]})
        self._refresh()
        self.assertEqual(bill.ipr_state, 'approved')
        self.assertFalse(self._resets(bill))

    def test_list_write_resets_each_bill(self):
        """FR-IPR-0018: one write on several checked drafts resets each of them once."""
        first = self._approved_bill('LIST-1')
        second = self._approved_bill('LIST-2')
        (first | second).write({'ref': 'SAME-NEW-REF'})
        self._refresh()
        for bill in (first, second):
            self.assertEqual(bill.ipr_state, 'not_checked')
            self.assertEqual(len(self._resets(bill)), 1)

    def test_posted_bill_not_reset(self):
        """FR-IPR-0018: a posted bill keeps its status when its reference changes."""
        bill = self._approved_bill('RESET-POSTED')
        bill.with_user(self.accountant).action_post()
        self._refresh()
        self.assertEqual(bill.state, 'posted')
        bill.write({'ref': 'RESET-POSTED-2'})
        self._refresh()
        self.assertEqual(bill.ipr_state, 'approved')
        self.assertFalse(self._resets(bill))
