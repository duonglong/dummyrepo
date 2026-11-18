"""Acceptance tests of T-010 (supplier portal access), written from the specification.

Requirements: BR-SPM-0002, BR-SPM-0003, FR-SPM-0016, FR-SPM-0017, FR-SPM-0027,
FR-SPM-0028, FR-SPM-0029, NFR-SPM-0001

Pages are driven with the HTTP client (the portal is the interface under test):
the home is /my/procurement, an order is Odoo's own /my/purchase/<id>. The
quote-request part of SC-SPM-0016-1 needs sourcing events and is tested in
test_sourcing_event_acceptance (T-013); SC-SPM-0017-2, 0017-3, 0027-1 and
0028-1 need quotations or events and are in test_quotation_versions_acceptance
(T-014). The grant dialog of SC-SPM-0029 is Odoo's portal.wizard, driven with Form.
"""
from odoo.exceptions import UserError
from odoo.tests import Form

from .portal_common import PASSWORD, SpmPortalCase

MODULE = 'supplier_procurement_management_hub'
REFUSED = (302, 303, 401, 403, 404)


class TestPortalHomeAcceptance(SpmPortalCase):
    """Requirements: FR-SPM-0016, FR-SPM-0017, FR-SPM-0027, FR-SPM-0028, BR-SPM-0002"""

    def test_sc_0016_1_home_lists_only_own_orders(self):
        """SC-SPM-0016-1 (orders part): Acme has 2 orders, Beta 3; an Acme
        portal user sees the 2 orders and nothing of Beta."""
        acme_orders = self._order(self.acme) | self._order(self.acme)
        beta_orders = self._order(self.beta) | self._order(self.beta) | self._order(self.beta)
        self._login(self.acme_user)
        response = self._get('/my/procurement')
        self.assertEqual(response.status_code, 200)
        for order in acme_orders:
            self.assertIn(order.name, response.text)
        for order in beta_orders:
            self.assertNotIn(order.name, response.text)
        self.assertNotIn('Beta', response.text)

    def test_sc_0016_2_order_shows_receipt_and_paid_bill_only(self):
        """SC-SPM-0016-2: the order shows its receipt status and the paid
        posted bill; no journal item and no internal note."""
        order = self._order(self.acme)
        order.message_post(body='Acme is expensive internal', subtype_xmlid='mail.mt_note')
        line = order.order_line[:1]
        bill = self.env['account.move'].create({
            'move_type': 'in_invoice', 'partner_id': self.acme.id,
            'invoice_date': '2026-11-10',
            'invoice_line_ids': [(0, 0, {
                'product_id': self.chair.id, 'name': 'Chair', 'quantity': 5.0,
                'price_unit': 10.0, 'purchase_line_id': line.id, 'tax_ids': [(6, 0, [])]})],
        })
        bill.action_post()
        payment = self.env['account.payment.register'].with_context(
            active_model='account.move', active_ids=bill.ids).create({})
        payment._create_payments()
        self.assertEqual(bill.payment_state, 'paid')
        self._login(self.acme_user)
        text = self._get(f'/my/purchase/{order.id}').text
        self.assertIn(bill.name, text)
        self.assertIn('paid', text.lower())
        self.assertIn('receipt', text.lower())
        self.assertNotIn('Acme is expensive internal', text)
        self.assertNotIn('journal item', text.lower())
        home = self._get('/my/procurement').text  # the hub home lists the order with its bill status
        self.assertIn(order.name, home)
        self.assertIn(bill.name, home)
        self.assertIn('paid', home.lower())
        self.assertNotIn('Acme is expensive internal', home)

    def test_sc_0017_1_foreign_order_is_not_found_and_logged(self):
        """SC-SPM-0017-1: an Acme user opening Beta's order gets a not found
        page and the portal activity log holds the refused attempt."""
        beta_order = self._order(self.beta)
        self._login(self.acme_user)
        response = self._get(f'/my/purchase/{beta_order.id}')
        self.assertEqual(response.status_code, 404)
        self.assertNotIn(beta_order.name, response.text)
        entry = self.env['procurement.portal.log'].sudo().search([
            ('partner_id', '=', self.acme.id), ('res_model', '=', 'purchase.order'),
            ('res_id', '=', beta_order.id)])
        self.assertTrue(entry)
        self.assertEqual(entry[:1].action, 'denied')
        self.assertEqual(entry[:1].user_id, self.acme_user)

    def test_sc_0017_1_unknown_order_answers_like_a_foreign_one(self):
        """FR-SPM-0017: a record that does not exist is answered like one of
        another supplier (nothing confirms the record exists)."""
        beta_order = self._order(self.beta)
        self._login(self.acme_user)
        missing = self._get('/my/purchase/99999999')
        foreign = self._get(f'/my/purchase/{beta_order.id}')
        self.assertEqual(missing.status_code, foreign.status_code)
        self.assertEqual(foreign.status_code, 404)

    def test_fr_0027_opening_the_home_is_logged(self):
        """FR-SPM-0027: opening the portal writes an entry with the user, the
        supplier and the time."""
        self._login(self.acme_user)
        self._get('/my/procurement')
        self.env.flush_all()
        entry = self.env['procurement.portal.log'].sudo().search([
            ('user_id', '=', self.acme_user.id), ('action', '=', 'view')], limit=1)
        self.assertTrue(entry)
        self.assertEqual(entry.partner_id, self.acme)
        self.assertTrue(entry.date)
        self.assertEqual(entry.result, 'ok')

    def test_fr_0028_blocked_supplier_is_refused_on_the_next_request(self):
        """FR-SPM-0028: after a Procurement manager blocks Acme, its logged-in
        user is refused on the next request."""
        order = self._order(self.acme)
        self._login(self.acme_user)
        self.assertEqual(self._get('/my/procurement').status_code, 200)
        self.acme.with_user(self.manager).action_spm_set_status(self.blocked, 'Fraud suspected')
        response = self._get('/my/procurement')
        self.assertIn(response.status_code, REFUSED)
        self.assertNotIn(order.name, response.text)

    def test_fr_0028_archived_user_is_refused_on_the_next_request(self):
        """FR-SPM-0028: an archived portal user with an open session is refused."""
        order = self._order(self.acme)
        self._login(self.acme_user)
        self.assertEqual(self._get('/my/procurement').status_code, 200)
        self.acme_user.sudo().write({'active': False})
        response = self._get('/my/procurement')
        self.assertIn(response.status_code, REFUSED)
        self.assertNotIn(order.name, response.text)

    def test_fr_0028_a_contact_of_a_non_supplier_gets_no_supplier_portal(self):
        """BR-SPM-0002: a portal user of a company that is not a supplier owns nothing."""
        customer = self.env['res.partner'].create({'name': 'Customer Co', 'is_company': True})
        user = self._portal_user(customer, 'Cy Customer', 'spm_customer_m2')
        order = self._order(self.acme)
        self._login(self.acme_user)
        self.assertEqual(self._get('/my/procurement').status_code, 200)  # the supplier's own user is let in
        self.authenticate(user.login, PASSWORD)
        response = self._get('/my/procurement')
        self.assertNotEqual(response.status_code, 200)
        self.assertNotIn(order.name, response.text)


class TestPortalInvitationAcceptance(SpmPortalCase):
    """Requirements: FR-SPM-0029"""

    def _grant(self, contact):
        """Open Odoo's portal dialog as the Buyer on the contact and grant access.

        The dialog is Odoo's portal.wizard; its rows are created by its default.
        """
        wizard_model = self.env['portal.wizard'].with_user(self.buyer).with_context(
            active_model='res.partner', active_ids=[contact.id], default_partner_ids=[contact.id])
        wizard = Form(wizard_model).save()
        rows = wizard.user_ids.filtered(lambda row: row.partner_id == contact)
        self.assertTrue(rows)
        rows.action_grant_access()

    def test_sc_0029_1_buyer_grants_a_supplier_contact_access(self):
        """SC-SPM-0029-1: Jane of Acme receives Odoo's portal invitation and,
        once signed up, sees Acme's supplier portal."""
        jane = self.env['res.partner'].create({
            'name': 'Jane New', 'parent_id': self.acme.id, 'type': 'contact',
            'email': 'jane.new@supplier.example'})
        self._grant(jane)
        user = self.env['res.users'].sudo().search([('partner_id', '=', jane.id)])
        self.assertTrue(user)
        self.assertEqual(user.group_ids & self.env.ref('base.group_portal'),
                         self.env.ref('base.group_portal'))
        self.assertTrue(self._sent_to(jane, 'token='))
        user.sudo().write({'password': PASSWORD})  # the sign-up step: she chooses a password
        self.authenticate(user.login, PASSWORD)
        response = self._get('/my/procurement')
        self.assertEqual(response.status_code, 200)
        self.assertIn('Acme', response.text)

    def test_sc_0029_2_contact_of_a_non_supplier_is_refused(self):
        """SC-SPM-0029-2: granting portal access to Tom of a customer that is
        not a supplier is refused with a message that he is not a supplier's."""
        customer = self.env['res.partner'].create({'name': 'Customer Co', 'is_company': True})
        tom = self.env['res.partner'].create({
            'name': 'Tom', 'parent_id': customer.id, 'type': 'contact',
            'email': 'tom@customer.example'})
        with self.assertRaises(UserError) as ctx:
            self._grant(tom)
        self.assertIn('supplier', str(ctx.exception).lower())
        self.assertFalse(self.env['res.users'].sudo().with_context(active_test=False).search(
            [('partner_id', '=', tom.id)]))
