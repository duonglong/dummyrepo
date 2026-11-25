from werkzeug.exceptions import NotFound

from odoo import http
from odoo.exceptions import UserError, ValidationError
from odoo.http import request

from ..services import portal_access
from .portal import SupplierPortal

QUOTATION_TEMPLATE = 'supplier_procurement_management_hub.portal_quotation'


class SupplierPortalQuotation(SupplierPortal):
    """The quotation page of an invited supplier, its submission and the files of a quotation.

    Requirements: FR-SPM-0017, FR-SPM-0020, FR-SPM-0021, FR-SPM-0022, FR-SPM-0027
    The response is the one of the session user's supplier for the event of the address; no id
    in the form decides it.
    """

    def _refused_page(self, refusal):
        """The refusal page (the refusal is logged by the access service)."""
        return self._refusal_page(404 if refusal.reason == 'not found' else 403)

    def _render_quotation(self, response, errors=None, saved=False):
        values = self._prepare_portal_layout_values()
        values.update(portal_access.quote_page_values(request.env, response))
        values.update({'page_name': 'spm_quotation', 'errors': errors or [], 'saved': saved})
        return request.render(QUOTATION_TEMPLATE, values, status=400 if errors else 200)

    @http.route('/my/procurement/rfq/<int:event_id>', type='http', auth='user', website=True, methods=['GET'])
    def spm_portal_quotation(self, event_id, **kw):
        """Requirements: FR-SPM-0017, FR-SPM-0020"""
        try:
            supplier, response = portal_access.owned_response(request.env, event_id)
        except portal_access.SupplierAccessRefused as refusal:
            return self._refused_page(refusal)
        portal_access.log(request.env, 'view', 'ok', supplier=supplier,
                          res_model=portal_access.EVENT_MODEL, res_id=event_id)
        return self._render_quotation(response, saved=bool(kw.get('saved')))

    @http.route('/my/procurement/rfq/<int:event_id>/submit', type='http', auth='user', website=True,
                methods=['POST'])
    def spm_portal_quotation_submit(self, event_id, **post):
        """Store the form as a new version of the user's own response, or refuse with a message.

        Requirements: FR-SPM-0020, FR-SPM-0021, FR-SPM-0022
        """
        env = request.env
        try:
            supplier, response = portal_access.owned_response(env, event_id)
        except portal_access.SupplierAccessRefused as refusal:
            return self._refused_page(refusal)
        lines, terms = portal_access.parse_quotation_post(env, response, post)
        try:
            with env.cr.savepoint():
                attachments = portal_access.create_quote_attachments(
                    env, request.httprequest.files.getlist('attachments'))
                version = response.spm_submit_version(lines, attachment_ids=attachments.ids, **terms)
        except (ValueError, UserError, ValidationError) as error:
            portal_access.log(env, 'submit', 'refused', supplier=supplier,
                              res_model=portal_access.EVENT_MODEL, res_id=event_id)
            return self._render_quotation(response, [str(error)])
        portal_access.log(env, 'submit', 'ok', supplier=supplier, record=version)
        return request.redirect(f'/my/procurement/rfq/{event_id}?saved=1')

    @http.route('/my/procurement/quote-file/<int:attachment_id>', type='http', auth='user', website=True)
    def spm_portal_quote_file(self, attachment_id, **kw):
        """A file of an invited event or of the user's own quotation; anything else answers not found.

        Requirements: BR-SPM-0002, FR-SPM-0017, FR-SPM-0027
        """
        try:
            attachment = portal_access.check_quote_file(request.env, attachment_id)
        except portal_access.SupplierAccessRefused as refusal:
            return self._refused_page(refusal)
        if not attachment.raw:
            raise NotFound()
        stream = request.env['ir.binary']._get_stream_from(attachment, 'raw')
        response = stream.get_response(as_attachment=True)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response
