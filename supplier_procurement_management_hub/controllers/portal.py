from odoo import http
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
            if not portal_access.is_supplier_user(request.env.user) or portal_access.is_blocked(request.env.user):
                return 'purchase.order', [('id', '=', 0)], 'read'
            return 'purchase.order', [('state', 'in', portal_access.VISIBLE_ORDER_STATES)], 'read'
        return super()._prepare_portal_counter_values(counter)

    def _refuse(self, refusal, record=None, res_model=None, res_id=None):
        """Log the refusal and answer with a page that tells nothing about the record."""
        portal_access.log(request.env, 'denied', 'refused', record=record,
                          res_model=res_model, res_id=res_id, detail=refusal.reason)
        status = 404 if refusal.reason == 'not found' else 403
        return request.render(REFUSED_TEMPLATE, {'status': status}, status=status)

    def _supplier_list_refusal(self):
        """The refusal page when a supplier user may not use the portal; None otherwise.

        Requirements: FR-SPM-0028
        """
        if not portal_access.is_supplier_user(request.env.user):
            return None
        try:
            portal_access.current_supplier(request.env)
        except portal_access.SupplierAccessRefused as refusal:
            return self._refuse(refusal)
        return None

    def _owned_order_or_refusal(self, order_id):
        """Return (supplier, order, refusal page); the page is set when the order is not the user's own.

        Requirements: BR-SPM-0002, FR-SPM-0017, FR-SPM-0028
        """
        try:
            supplier = portal_access.current_supplier(request.env)
        except portal_access.SupplierAccessRefused as refusal:
            return None, None, self._refuse(refusal, res_model='purchase.order', res_id=order_id)
        order = portal_access.get_owned(
            request.env, 'purchase.order', order_id,
            [('state', 'in', portal_access.VISIBLE_ORDER_STATES)])
        if not order:
            return None, None, self._refuse(portal_access.SupplierAccessRefused('not found'),
                                            res_model='purchase.order', res_id=order_id)
        return supplier, order, None

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
        user = request.env.user
        if not portal_access.is_supplier_user(user):
            return super().portal_my_purchase_order(order_id=order_id, access_token=access_token, **kw)
        supplier, order, refusal_page = self._owned_order_or_refusal(order_id)
        if refusal_page:
            return refusal_page
        action = 'download' if kw.get('report_type') in ('pdf', 'text') else 'view'
        portal_access.log(request.env, action, 'ok', supplier=supplier, record=order)
        # the token is dropped: ownership alone decides for a supplier user
        return super().portal_my_purchase_order(order_id=order_id, access_token=None, **kw)

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
        if not portal_access.is_supplier_user(request.env.user):
            return super().portal_my_purchase_order_update_dates(
                order_id=order_id, access_token=access_token, **kw)
        supplier, order, refusal_page = self._owned_order_or_refusal(order_id)
        if refusal_page:
            return refusal_page
        portal_access.log(request.env, 'submit', 'ok', supplier=supplier, record=order)
        return super().portal_my_purchase_order_update_dates(order_id=order_id, access_token=None, **kw)

    @http.route()
    def portal_my_purchase_order_download_edi(self, order_id=None, access_token=None, **kw):
        """Own orders only for a supplier user; the token is ignored.

        Requirements: BR-SPM-0002, FR-SPM-0017, FR-SPM-0027, FR-SPM-0028
        """
        if not portal_access.is_supplier_user(request.env.user):
            return super().portal_my_purchase_order_download_edi(
                order_id=order_id, access_token=access_token, **kw)
        supplier, order, refusal_page = self._owned_order_or_refusal(order_id)
        if refusal_page:
            return refusal_page
        portal_access.log(request.env, 'download', 'ok', supplier=supplier, record=order)
        return super().portal_my_purchase_order_download_edi(order_id=order_id, access_token=None, **kw)

    def _purchase_order_get_page_view_values(self, order, access_token, **kwargs):
        values = super()._purchase_order_get_page_view_values(order, access_token, **kwargs)
        if portal_access.is_supplier_user(request.env.user):
            values['spm_order_row'] = portal_access.order_rows(request.env, order)[0]
        return values
