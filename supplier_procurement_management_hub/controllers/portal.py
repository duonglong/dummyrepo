from odoo import SUPERUSER_ID, http
from odoo.exceptions import AccessError, MissingError
from odoo.http import request

from odoo.addons.portal.controllers.portal import pager as portal_pager
from odoo.addons.purchase.controllers.portal import CustomerPortal

from ..services import portal_access

REFUSED_TEMPLATE = 'supplier_procurement_management_hub.portal_refused'


class SupplierPortal(CustomerPortal):
    """The supplier portal: home, ownership of orders and the activity log.

    Requirements: BR-SPM-0002, BR-SPM-0003, FR-SPM-0016, FR-SPM-0017, FR-SPM-0027, FR-SPM-0028
    Ownership is decided here from the session user; an id in the address is only searched
    with the user's own rights.
    """

    def _prepare_portal_counter_values(self, counter):
        if counter == 'spm_order_count':
            if not portal_access.is_supplier_user(request.env.user) or portal_access.refusal(request.env):
                return 'purchase.order', [('id', '=', 0)], 'read'
            return 'purchase.order', [('state', 'in', portal_access.VISIBLE_ORDER_STATES)], 'read'
        return super()._prepare_portal_counter_values(counter)

    def _refusal_page(self, status):
        """A page that tells nothing about the record (the refusal is logged by the access service)."""
        return request.render(REFUSED_TEMPLATE, {'status': status}, status=status)

    def _refuse(self, refusal):
        """Log the refusal of the user and answer with a page that tells nothing."""
        portal_access.log(request.env, 'denied', 'refused', detail=refusal.reason)
        return self._refusal_page(404 if refusal.reason == 'not found' else 403)

    def _supplier_list_refusal(self):
        """The refusal page when a supplier user may not use the portal; None otherwise.

        Requirements: FR-SPM-0028
        """
        if not portal_access.is_supplier_user(request.env.user):
            return None
        refused = portal_access.refusal(request.env)
        return self._refuse(refused) if refused else None

    def _is_supplier_document(self, model_name, document_id):
        return portal_access.is_supplier_user(request.env.user) \
            and portal_access.is_procurement_record(request.env, model_name, document_id)

    def _supplier_document(self, model_name, document_id, action):
        """The supplier user's own document with full rights (the native contract), checked and logged.

        Requirements: BR-SPM-0002, FR-SPM-0017, FR-SPM-0027, FR-SPM-0028
        Raises MissingError when it is not the user's own, AccessError when the user is refused.
        """
        try:
            document = portal_access.check_document(request.env, model_name, document_id, action)
        except portal_access.SupplierAccessRefused as refused:
            if refused.reason == 'not found':
                raise MissingError(request.env._("This document does not exist.")) from None
            raise AccessError(request.env._("You no longer have access to the supplier portal.")) from None
        return document.with_user(SUPERUSER_ID)

    def _document_check_access(self, model_name, document_id, access_token=None):
        """A supplier user reaches only procurement documents the user's own rights allow; a token is ignored.

        Requirements: BR-SPM-0002, FR-SPM-0017, FR-SPM-0027, FR-SPM-0028

        This one check serves every native portal page of an order, a vendor bill or an attachment of
        them. Other documents (customer invoices, sales, shared links) keep Odoo's own check.
        """
        if not self._is_supplier_document(model_name, document_id):
            return super()._document_check_access(model_name, document_id, access_token=access_token)
        checked = getattr(request, 'spm_checked_document', None)
        if checked is not None and checked._name == model_name and checked.id == document_id:
            return checked  # the route of this request has checked and logged it already
        action = self._access_action(request.params)
        return self._supplier_document(model_name, document_id, action)

    @staticmethod
    def _access_action(params):
        """The log action of a document request: a pdf or text report is a download, the rest a view."""
        return 'download' if params.get('report_type') in ('pdf', 'text') else 'view'

    def _supplier_route_refusal(self, model_name, document_id, action):
        """For a native document route: the refusal page, or None once the document is checked and logged.

        Requirements: BR-SPM-0002, FR-SPM-0017, FR-SPM-0027, FR-SPM-0028
        """
        request.spm_checked_document = None
        try:
            request.spm_checked_document = self._supplier_document(model_name, document_id, action)
        except MissingError:
            return self._refusal_page(404)
        except AccessError:
            return self._refusal_page(403)
        return None

    def _profile_values(self, supplier):
        """Plain values of the supplier's own profile: no risk level, no internal data."""
        sudo_supplier = supplier.sudo()  # sudo: profile of the user's own company contact, plain values only
        return {
            'name': sudo_supplier.name,
            'status': sudo_supplier.spm_status_id.name,
            'compliance': dict(sudo_supplier._fields['spm_compliance_state']._description_selection(
                sudo_supplier.env)).get(sudo_supplier.spm_compliance_state, ''),
            'email': sudo_supplier.email,
            'phone': sudo_supplier.phone,
        }

    @http.route(['/my/procurement', '/my/procurement/page/<int:page>'], type='http', auth='user', website=True)
    def spm_portal_home(self, page=1, **kw):
        """The supplier's own profile, documents and orders with receipt and bill status.

        Requirements: FR-SPM-0016, FR-SPM-0017, FR-SPM-0027, FR-SPM-0028
        """
        try:
            supplier = portal_access.current_supplier(request.env)
        except portal_access.SupplierAccessRefused as refusal:
            return self._refuse(refusal)
        domain = [('state', 'in', portal_access.VISIBLE_ORDER_STATES)]
        orders_model = request.env['purchase.order']
        pager = portal_pager(url='/my/procurement', total=orders_model.search_count(domain),
                             page=page, step=portal_access.PORTAL_PAGE_SIZE)
        orders = orders_model.search(domain, order='create_date desc, id desc',
                                     limit=portal_access.PORTAL_PAGE_SIZE, offset=pager['offset'])
        portal_access.log(request.env, 'view', 'ok', supplier=supplier, detail='home')
        values = self._prepare_portal_layout_values()
        values.update({
            'page_name': 'spm_home',
            'pager': pager,
            'profile': self._profile_values(supplier),
            'documents': request.env['procurement.supplier.document'].search(
                [('partner_id', '=', supplier.id), ('is_current', '=', True)]),
            'order_rows': portal_access.order_rows(request.env, orders),
        })
        return request.render('supplier_procurement_management_hub.portal_home', values)

    @http.route()
    def portal_my_purchase_order(self, order_id=None, access_token=None, **kw):
        """Own orders only for a supplier user; anything else answers not found and is logged.

        Requirements: BR-SPM-0002, FR-SPM-0017, FR-SPM-0027, FR-SPM-0028
        """
        if portal_access.is_supplier_user(request.env.user):
            action = self._access_action(kw)
            if refusal_page := self._supplier_route_refusal('purchase.order', order_id, action):
                return refusal_page
            access_token = None  # ownership alone decides for a supplier user
        return super().portal_my_purchase_order(order_id=order_id, access_token=access_token, **kw)

    @http.route()
    def portal_my_purchase_orders(self, *args, **kw):
        """Requirements: FR-SPM-0028"""
        return self._supplier_list_refusal() or super().portal_my_purchase_orders(*args, **kw)

    @http.route()
    def portal_my_requests_for_quotation(self, *args, **kw):
        """Requirements: FR-SPM-0028"""
        return self._supplier_list_refusal() or super().portal_my_requests_for_quotation(*args, **kw)

    @http.route()
    def portal_my_purchase_order_update_dates(self, order_id=None, access_token=None, **kw):
        """Own orders only for a supplier user; the token is ignored.

        Requirements: BR-SPM-0002, FR-SPM-0017, FR-SPM-0027, FR-SPM-0028
        """
        if portal_access.is_supplier_user(request.env.user):
            if refusal_page := self._supplier_route_refusal('purchase.order', order_id, 'submit'):
                return refusal_page
            access_token = None
        return super().portal_my_purchase_order_update_dates(order_id=order_id, access_token=access_token, **kw)

    @http.route()
    def portal_my_purchase_order_download_edi(self, order_id=None, access_token=None, **kw):
        """Own orders only for a supplier user; the token is ignored.

        Requirements: BR-SPM-0002, FR-SPM-0017, FR-SPM-0027, FR-SPM-0028
        """
        if portal_access.is_supplier_user(request.env.user):
            if refusal_page := self._supplier_route_refusal('purchase.order', order_id, 'download'):
                return refusal_page
            access_token = None
        return super().portal_my_purchase_order_download_edi(order_id=order_id, access_token=access_token, **kw)

    def _purchase_order_get_page_view_values(self, order, access_token, **kwargs):
        values = super()._purchase_order_get_page_view_values(order, access_token, **kwargs)
        if portal_access.is_supplier_user(request.env.user):
            values['spm_order_row'] = portal_access.order_rows(request.env, order)[0]
        return values
