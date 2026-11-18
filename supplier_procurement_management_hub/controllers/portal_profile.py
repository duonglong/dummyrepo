from werkzeug.exceptions import NotFound

from odoo import http
from odoo.exceptions import AccessError, MissingError
from odoo.http import request

from ..services import onboarding, portal_access
from .portal import SupplierPortal

PROFILE_TEMPLATE = 'supplier_procurement_management_hub.portal_profile'


class SupplierPortalProfile(SupplierPortal):
    """The supplier's own profile edit, document upload and file download.

    Requirements: FR-SPM-0015, FR-SPM-0018, FR-SPM-0019, NFR-SPM-0003
    The supplier is the session user's company contact; nothing in the request names it.
    """

    def _render_profile(self, supplier, errors=None, saved=False):
        """The profile page: editable fields, bank details and the upload form."""
        env = request.env
        document_types = env['procurement.document.type'].search([('portal_upload', '=', True)])
        values = self._prepare_portal_layout_values()
        values.update({
            'page_name': 'spm_profile',
            'supplier_name': supplier.sudo().name,  # sudo: the own company contact's name
            'fields': [{
                'name': name, 'label': field._description_string(env), 'type': field.type,
                'value': supplier.sudo()[name] or '',  # sudo: plain values of the own company contact
                'options': dict(field._description_selection(env)) if field.type == 'selection' else {},
            } for name, field in portal_access.editable_fields(env).items()],
            'document_types': document_types,
            'max_mb': onboarding.UPLOAD_MAX_MB,
            'errors': errors or [],
            'saved': saved,
        })
        return request.render(PROFILE_TEMPLATE, values, status=400 if errors else 200)

    def _profile_supplier(self):
        """Return ``(supplier, None)`` or ``(None, refusal page)`` for the session user."""
        try:
            return portal_access.current_supplier(request.env), None
        except portal_access.SupplierAccessRefused as refusal:
            return None, self._refuse(refusal)

    @http.route('/my/procurement/profile', type='http', auth='user', website=True, methods=['GET'])
    def spm_portal_profile(self, **kw):
        """Requirements: FR-SPM-0018"""
        supplier, refusal_page = self._profile_supplier()
        return refusal_page or self._render_profile(supplier, saved=bool(kw.get('saved')))

    @http.route('/my/procurement/profile', type='http', auth='user', website=True, methods=['POST'])
    def spm_portal_profile_save(self, **post):
        """Write the editable fields only and hold a bank change for verification.

        Requirements: FR-SPM-0015, FR-SPM-0018
        """
        supplier, refusal_page = self._profile_supplier()
        if refusal_page:
            return refusal_page
        errors = portal_access.update_profile(request.env, supplier, post)
        if errors:
            return self._render_profile(supplier, errors)
        return request.redirect('/my/procurement/profile?saved=1')

    @http.route('/my/procurement/documents/upload', type='http', auth='user', website=True, methods=['POST'])
    def spm_portal_document_upload(self, **post):
        """Store a checked upload as a To verify document of the supplier.

        Requirements: FR-SPM-0019, NFR-SPM-0003
        """
        supplier, refusal_page = self._profile_supplier()
        if refusal_page:
            return refusal_page
        try:
            portal_access.upload_document(
                request.env, supplier, post, request.httprequest.files.get('file'))
        except ValueError as error:
            portal_access.log(request.env, 'submit', 'refused', supplier=supplier, detail='upload')
            return self._render_profile(supplier, [str(error)])
        return request.redirect('/my/procurement/profile?saved=1')

    @http.route('/my/procurement/file/<int:document_id>', type='http', auth='user', website=True)
    def spm_portal_document_file(self, document_id, **kw):
        """The file of one of the supplier's own documents; anything else answers not found.

        Requirements: NFR-SPM-0003, FR-SPM-0017
        """
        try:
            document = self._supplier_document('procurement.supplier.document', document_id, 'download')
        except (AccessError, MissingError):  # every refusal looks the same to the caller
            return self._refusal_page(404)
        attachment = document.attachment_id
        if not attachment:
            raise NotFound()
        stream = request.env['ir.binary']._get_stream_from(attachment, 'raw')
        response = stream.get_response(as_attachment=True)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response
