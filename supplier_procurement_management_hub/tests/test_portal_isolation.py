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
