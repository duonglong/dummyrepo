"""Own tests of T-010 (supplier portal access).

Requirements: BR-SPM-0002, BR-SPM-0003, FR-SPM-0016, FR-SPM-0017, FR-SPM-0027, FR-SPM-0028
"""
from odoo.exceptions import AccessError
from odoo.tests.common import JsonRpcException

from .portal_common import SpmPortalCase


class TestPortalAccessRules(SpmPortalCase):
    """Requirements: BR-SPM-0002, FR-SPM-0017, FR-SPM-0027, FR-SPM-0028"""

    def test_token_does_not_open_a_foreign_order_for_a_supplier_user(self):
        """FR-SPM-0017: the order's own access token does not let another supplier in."""
        beta_order = self._order(self.beta)
        token = beta_order.sudo()._portal_ensure_token()
        self._login(self.acme_user)
        response = self._get(f'/my/purchase/{beta_order.id}?access_token={token}')
        self.assertEqual(response.status_code, 404)
        self.assertNotIn(beta_order.name, response.text)

    def test_own_order_opening_is_logged_as_view(self):
        """FR-SPM-0027: opening an own order writes a view entry for the order."""
        order = self._order(self.acme)
        self._login(self.acme_user)
        self.assertEqual(self._get(f'/my/purchase/{order.id}').status_code, 200)
        entry = self.env['procurement.portal.log'].sudo().search([
            ('res_model', '=', 'purchase.order'), ('res_id', '=', order.id), ('action', '=', 'view')])
        self.assertEqual(entry.result, 'ok')
        self.assertEqual(entry.partner_id, self.acme)

    def test_blocked_supplier_is_logged_as_refused(self):
        """FR-SPM-0028: the refusal of a blocked supplier is in the activity log."""
        self._login(self.acme_user)
        self.acme.with_user(self.manager).action_spm_set_status(self.blocked, 'Fraud suspected')
        self._get('/my/procurement')
        entry = self.env['procurement.portal.log'].sudo().search([
            ('user_id', '=', self.acme_user.id), ('action', '=', 'denied')])
        self.assertEqual(entry.detail, 'blocked')

    def test_documents_are_readable_for_own_supplier_only(self):
        """BR-SPM-0002: the record rule limits a portal user to the documents of its company."""
        doc_type = self.env['procurement.document.type'].create({'name': 'Certificate M2'})
        mine = self.env['procurement.supplier.document'].create(
            {'partner_id': self.acme.id, 'document_type_id': doc_type.id})
        theirs = self.env['procurement.supplier.document'].create(
            {'partner_id': self.beta.id, 'document_type_id': doc_type.id})
        documents = self.env['procurement.supplier.document'].with_user(self.acme_user)
        self.assertEqual(documents.search([('id', 'in', (mine | theirs).ids)]), mine)
        with self.assertRaises(AccessError):
            theirs.with_user(self.acme_user).read(['document_type_id'])

    def test_home_shows_no_risk_level(self):
        """BR-SPM-0003: the profile never carries the internal risk level."""
        self.acme.sudo().spm_risk_level = 'high'
        self._login(self.acme_user)
        text = self._get('/my/procurement').text
        self.assertNotIn('High', text)
        self.assertNotIn('risk', text.lower())


class TestPortalRoutesGuarded(SpmPortalCase):
    """Requirements: BR-SPM-0002, FR-SPM-0017, FR-SPM-0027, FR-SPM-0028"""

    def _denied(self, order):
        return self.env['procurement.portal.log'].sudo().search([
            ('user_id', '=', self.acme_user.id), ('res_id', '=', order.id), ('action', '=', 'denied')])

    def test_blocked_supplier_is_refused_on_native_lists(self):
        """FR-SPM-0028: the native order and quote-request lists answer a refusal once blocked."""
        order = self._order(self.acme)
        self._login(self.acme_user)
        self.acme.with_user(self.manager).action_spm_set_status(self.blocked, 'Fraud suspected')
        for url in ('/my/purchase', '/my/rfq'):
            response = self._get(url)
            self.assertEqual(response.status_code, 403, url)
            self.assertNotIn(order.name, response.text)

    def test_blocked_supplier_is_refused_on_edi_download(self):
        """FR-SPM-0028: a blocked supplier's user gets no EDI file of its own order."""
        order = self._order(self.acme)
        self._login(self.acme_user)
        self.acme.with_user(self.manager).action_spm_set_status(self.blocked, 'Fraud suspected')
        self.assertEqual(self._get(f'/my/purchase/{order.id}/download_edi').status_code, 403)

    def test_foreign_token_gives_no_edi_download(self):
        """BR-SPM-0002, FR-SPM-0017: a token of another supplier's order opens nothing and is logged."""
        beta_order = self._order(self.beta)
        token = beta_order.sudo()._portal_ensure_token()
        self._login(self.acme_user)
        response = self._get(f'/my/purchase/{beta_order.id}/download_edi?access_token={token}')
        self.assertEqual(response.status_code, 404)
        self.assertTrue(self._denied(beta_order))

    def test_own_edi_download_is_logged(self):
        """FR-SPM-0027: downloading the EDI file of an own order writes a download entry."""
        order = self._order(self.acme)
        self._login(self.acme_user)
        self._get(f'/my/purchase/{order.id}/download_edi')
        entry = self.env['procurement.portal.log'].sudo().search([
            ('res_id', '=', order.id), ('action', '=', 'download')])
        self.assertEqual(entry.result, 'ok')

    def test_login_of_a_supplier_user_is_logged(self):
        """FR-SPM-0027: a supplier user's login writes a login entry for the supplier."""
        self._login(self.acme_user)
        entry = self.env['procurement.portal.log'].sudo().search([
            ('user_id', '=', self.acme_user.id), ('action', '=', 'login')])
        self.assertEqual(entry.partner_id, self.acme)

    def test_external_call_reads_nothing_of_another_supplier(self):
        """NFR-SPM-0001: a JSON-RPC read of a foreign document by an Acme session returns no data."""
        doc_type = self.env['procurement.document.type'].create({'name': 'Certificate RPC'})
        theirs = self.env['procurement.supplier.document'].create(
            {'partner_id': self.beta.id, 'document_type_id': doc_type.id})
        self._login(self.acme_user)
        with self.assertRaises(JsonRpcException):
            self.make_jsonrpc_request('/web/dataset/call_kw/procurement.supplier.document/read', {
                'model': 'procurement.supplier.document', 'method': 'read',
                'args': [theirs.ids, ['document_type_id']], 'kwargs': {}})


class TestPortalNativeDocumentPaths(SpmPortalCase):
    """Requirements: BR-SPM-0002, FR-SPM-0017, FR-SPM-0028, NFR-SPM-0001"""

    def _bill(self, supplier):
        order = self._order(supplier)
        bill = self.env['account.move'].create({
            'move_type': 'in_invoice', 'partner_id': supplier.id, 'invoice_date': '2026-11-10',
            'invoice_line_ids': [(0, 0, {
                'product_id': self.chair.id, 'name': 'Chair', 'quantity': 5.0, 'price_unit': 10.0,
                'purchase_line_id': order.order_line[:1].id, 'tax_ids': [(6, 0, [])]})],
        })
        bill.action_post()
        return bill

    def _denied(self, model, res_id):
        return self.env['procurement.portal.log'].sudo().search([
            ('user_id', '=', self.acme_user.id), ('res_model', '=', model),
            ('res_id', '=', res_id), ('action', '=', 'denied')])

    def test_foreign_bill_token_opens_nothing(self):
        """BR-SPM-0002, FR-SPM-0017: a bill token of another supplier gives no bill and is logged."""
        bill = self._bill(self.beta)
        token = bill.sudo()._portal_ensure_token()
        self._login(self.acme_user)
        response = self._get(f'/my/invoices/{bill.id}?access_token={token}')
        self.assertEqual(response.status_code, 303)
        self.assertNotIn(bill.name, response.text)
        self.assertTrue(self._denied('account.move', bill.id))

    def test_own_bill_opens(self):
        """FR-SPM-0016: the own posted bill is still reachable."""
        bill = self._bill(self.acme)
        self._login(self.acme_user)
        self.assertEqual(self._get(f'/my/invoices/{bill.id}').status_code, 200)

    def test_blocked_supplier_is_refused_on_bills(self):
        """FR-SPM-0028: a blocked supplier's user gets neither the bill list nor an own bill."""
        bill = self._bill(self.acme)
        self._login(self.acme_user)
        self.acme.with_user(self.manager).action_spm_set_status(self.blocked, 'Fraud suspected')
        response = self._get('/my/invoices')
        self.assertEqual(response.status_code, 403)
        self.assertNotIn(bill.name, response.text)
        self.assertEqual(self._get(f'/my/invoices/{bill.id}').status_code, 303)
        self.assertTrue(self._denied('account.move', bill.id))

    def test_foreign_token_gives_no_chatter(self):
        """BR-SPM-0002, NFR-SPM-0001: a token of another supplier's order opens no thread."""
        beta_order = self._order(self.beta)
        token = beta_order.sudo()._portal_ensure_token()
        orders = self.env['purchase.order'].with_user(self.acme_user)
        self.assertFalse(orders._get_thread_with_access(beta_order.id, token=token))

    def test_blocked_supplier_gets_no_chatter_on_own_order(self):
        """FR-SPM-0028: once blocked, the user's own order thread cannot be read nor posted to."""
        order = self._order(self.acme)
        orders = self.env['purchase.order'].with_user(self.acme_user)
        self.assertEqual(orders._get_thread_with_access(order.id), order)
        self.acme.with_user(self.manager).action_spm_set_status(self.blocked, 'Fraud suspected')
        self.assertFalse(orders._get_thread_with_access(order.id))
        self._login(self.acme_user)
        with self.assertRaises(JsonRpcException):
            self.make_jsonrpc_request('/mail/message/post', {
                'thread_model': 'purchase.order', 'thread_id': order.id,
                'post_data': {'body': 'hello', 'message_type': 'comment'}})
        self.assertFalse(order.message_ids.filtered(lambda m: 'hello' in (m.body or '')))

    def test_date_update_post_of_a_foreign_order_changes_nothing(self):
        """BR-SPM-0002, FR-SPM-0017, NFR-SPM-0001: the update form post with another supplier's token
        leaves its planned dates unchanged and is logged."""
        beta_order = self._order(self.beta)
        line = beta_order.order_line[:1]
        planned = line.date_planned
        token = beta_order.sudo()._portal_ensure_token()
        self._login(self.acme_user)
        self.make_jsonrpc_request(
            f'/my/purchase/{beta_order.id}/update', {'access_token': token, str(line.id): '2030-01-01'})
        self.assertEqual(line.date_planned, planned)
        self.assertTrue(self._denied('purchase.order', beta_order.id))

    def test_procurement_card_is_hidden_for_non_suppliers_and_blocked(self):
        """FR-SPM-0028: the counter of the Procurement card is 0 for a customer contact and a blocked supplier."""
        self._order(self.acme)
        customer = self.env['res.partner'].create({'name': 'Customer Co', 'is_company': True})
        customer_user = self._portal_user(customer, 'Cy Customer', 'spm_customer_m2')
        self._login(self.acme_user)
        counters = self.make_jsonrpc_request('/my/counters', {'counters': {'spm_order_count': 'card'}})
        self.assertEqual(counters['spm_order_count'], 1)
        self.acme.with_user(self.manager).action_spm_set_status(self.blocked, 'Fraud suspected')
        self.assertEqual(
            self.make_jsonrpc_request('/my/counters', {'counters': {'spm_order_count': 'card'}})['spm_order_count'], 0)
        self._login(customer_user)
        self.assertEqual(
            self.make_jsonrpc_request('/my/counters', {'counters': {'spm_order_count': 'card'}})['spm_order_count'], 0)
