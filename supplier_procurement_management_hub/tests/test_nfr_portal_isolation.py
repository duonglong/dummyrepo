"""Portal isolation sweep: one supplier's session against every layer holding another supplier's data.

Requirements: NFR-SPM-0001
The fixtures of the other supplier (Beta) are an order, a quote request with its response and an
uploaded document with its file. A session of Acme, not invited and not owner, asks for each of
them through the portal pages, the form posts, the download addresses, the chatter and the
generic data calls; none may answer with a word of Beta's.
"""
from odoo.tests.common import JsonRpcException

from .portal_common import SpmPortalCase

MARKER = 'Beta-only-marker'


class TestPortalIsolationSweep(SpmPortalCase):
    """Requirements: NFR-SPM-0001"""

    def setUp(self):
        super().setUp()
        self.beta_order = self._order(self.beta)
        self.beta_event = self._event(self.beta, title=f'{MARKER} chairs')
        self.beta_response = self._response(self.beta_event, self.beta)
        doc_type = self.env['procurement.document.type'].create({'name': 'Certificate sweep'})
        self.beta_document = self.env['procurement.supplier.document'].create(
            {'partner_id': self.beta.id, 'document_type_id': doc_type.id})
        self.beta_file = self.env['ir.attachment'].create({
            'name': f'{MARKER}.pdf', 'raw': MARKER.encode(),
            'res_model': 'procurement.supplier.document', 'res_id': self.beta_document.id})
        self.beta_document.attachment_id = self.beta_file
        self.beta_order.message_post(body=f'{MARKER} message', message_type='comment',
                                     subtype_xmlid='mail.mt_comment')
        self._login(self.acme_user)

    def _assert_nothing(self, response, what):
        self.assertNotIn(response.status_code, (200, 201), what)
        for needle in (MARKER, self.beta_order.name):
            self.assertNotIn(needle, response.text, what)

    def test_nfr_0001_pages_and_downloads_give_nothing(self):
        """NFR-SPM-0001: every page and download address of Beta's records answers with nothing of it."""
        token = self.beta_order.sudo()._portal_ensure_token()  # sudo: test fixture, the token of another supplier's order
        addresses = [
            f'/my/purchase/{self.beta_order.id}',
            f'/my/purchase/{self.beta_order.id}?access_token={token}',
            f'/my/purchase/{self.beta_order.id}?report_type=pdf&download=true',
            f'/my/purchase/{self.beta_order.id}/download_edi?access_token={token}',
            f'/my/procurement/rfq/{self.beta_event.id}',
            f'/my/procurement/file/{self.beta_document.id}',
            f'/my/procurement/quote-file/{self.beta_file.id}',
            f'/web/content/{self.beta_file.id}',
            f'/web/content/{self.beta_file.id}?download=true',
        ]
        for address in addresses:
            self._assert_nothing(self._get(address), address)

    def test_nfr_0001_image_address_gives_no_file_content(self):
        """NFR-SPM-0001: the image address answers a placeholder at most, never the bytes of Beta's file."""
        response = self._get(f'/web/image/{self.beta_file.id}')
        self.assertNotIn(MARKER.encode(), response.content)

    def test_nfr_0001_lists_hold_nothing_of_beta(self):
        """NFR-SPM-0001: Acme's own lists and home page name no record of Beta."""
        for address in ('/my/procurement', '/my/purchase', '/my/rfq', '/my/procurement/profile'):
            response = self._get(address)
            self.assertNotIn(MARKER, response.text, address)
            self.assertNotIn(self.beta_order.name, response.text, address)

    def test_nfr_0001_form_posts_change_nothing(self):
        """NFR-SPM-0001: a quotation, a question and an acknowledgement posted for Beta's records are refused."""
        versions = self.beta_response.version_ids
        questions = self.env['procurement.sourcing.question'].search_count([])
        line = self.beta_event.line_ids
        posts = [
            (f'/my/procurement/rfq/{self.beta_event.id}/submit',
             {f'price_{line.id}': '1', f'lead_time_{line.id}': '1'}),
            (f'/my/procurement/rfq/{self.beta_event.id}/question', {'question': 'What is the price?'}),
            (f'/my/procurement/orders/{self.beta_order.id}/acknowledge', {}),
            (f'/my/procurement/orders/{self.beta_order.id}/propose', {}),
        ]
        for address, data in posts:
            response = self._post(address, data)
            self.assertNotEqual(response.status_code, 200, address)
            self.assertNotIn(MARKER, response.text, address)
        self.assertEqual(self.beta_response.version_ids, versions)
        self.assertEqual(self.env['procurement.sourcing.question'].search_count([]), questions)
        self.assertFalse(self.beta_order.spm_acknowledged_date)

    def test_nfr_0001_data_calls_return_no_row_of_beta(self):
        """NFR-SPM-0001: a generic data call of the open session finds none of Beta's rows, on any layer."""
        targets = {
            'purchase.order': self.beta_order.ids,
            'procurement.sourcing.event': self.beta_event.ids,
            'procurement.sourcing.response': self.beta_response.ids,
            'procurement.supplier.document': self.beta_document.ids,
            'ir.attachment': self.beta_file.ids,
        }
        for model, ids in targets.items():
            try:
                rows = self.make_jsonrpc_request(f'/web/dataset/call_kw/{model}/search_read', {
                    'model': model, 'method': 'search_read',
                    'args': [[('id', 'in', ids)], ['id']], 'kwargs': {}})
            except JsonRpcException:
                continue
            self.assertEqual(rows, [], model)

    def test_nfr_0001_chatter_returns_no_message(self):
        """NFR-SPM-0001: the chatter of Beta's order, asked for by an Acme session, returns no message."""
        store = self.make_jsonrpc_request('/mail/store', {'fetch_params': [['/mail/thread/messages', {
            'thread_model': 'purchase.order', 'thread_id': self.beta_order.id}]]})
        self.assertNotIn(MARKER, str(store))
        with self.assertRaises(JsonRpcException):
            self.make_jsonrpc_request('/mail/message/post', {
                'thread_model': 'purchase.order', 'thread_id': self.beta_order.id,
                'post_data': {'body': 'intrusion', 'message_type': 'comment'}})
        self.assertFalse(self.beta_order.message_ids.filtered(lambda m: 'intrusion' in (m.body or '')))

    def test_nfr_0001_the_other_session_still_reaches_its_own(self):
        """NFR-SPM-0001: the sweep is not trivially green: Beta's own session opens Beta's order and file."""
        self._login(self.beta_user)
        self.assertEqual(self._get(f'/my/purchase/{self.beta_order.id}').status_code, 200)
        self.assertEqual(self._get(f'/my/procurement/file/{self.beta_document.id}').status_code, 200)
