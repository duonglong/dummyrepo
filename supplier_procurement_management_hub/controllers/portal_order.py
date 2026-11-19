from datetime import datetime, time

from werkzeug.urls import url_encode

from odoo import http
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.http import request

from ..services import delivery, portal_access
from .portal_profile import SupplierPortalProfile

ORDER_URL = '/my/purchase/%d'
DATE_FORMAT = '%Y-%m-%d'


class SupplierPortalOrder(SupplierPortalProfile):
    """Acknowledge an order and propose delivery dates from the supplier's own order page.

    Requirements: FR-SPM-0024, FR-SPM-0025
    The supplier is the session user's company contact; the order id of the address is searched with
    the user's own rights, and the model methods check the supplier again.
    """

    def _order_or_refusal(self, order_id, action):
        """The supplier user's own order, or the refusal page (logged) when it is not theirs."""
        if not portal_access.is_supplier_user(request.env.user):
            return None, self._refusal_page(403)
        if refusal_page := self._supplier_route_refusal('purchase.order', order_id, action):
            return None, refusal_page
        return request.env['purchase.order'].browse(order_id), None

    @staticmethod
    def _day(value):
        """The date typed in a form field, or None when empty; raises ValueError when it is not a date."""
        value = (value or '').strip()
        return datetime.strptime(value, DATE_FORMAT).date() if value else None

    def _back(self, order, error=None, done=None):
        """Redirect to the order page with a one-word status; the page words it."""
        query = url_encode({k: v for k, v in (('spm_error', error), ('spm_done', done)) if v})
        return request.redirect(ORDER_URL % order.id + (f'?{query}' if query else ''))

    @http.route('/my/procurement/orders/<int:order_id>/acknowledge', type='http', auth='user',
                website=True, methods=['POST'])
    def spm_portal_acknowledge(self, order_id, **post):
        """Acknowledge the order with the confirmed date of each line.

        Requirements: FR-SPM-0024
        """
        order, refusal_page = self._order_or_refusal(order_id, 'submit')
        if refusal_page:
            return refusal_page
        commitments = order.sudo().spm_commitment_ids  # sudo: the line ids of an order the caller owns
        try:
            dates = {c.order_line_id.id: self._day(post.get(f'date_{c.order_line_id.id}')) for c in commitments}
            order.spm_acknowledge({line: day for line, day in dates.items() if day})
        except ValueError:
            return self._back(order, error='date')
        except (UserError, AccessError) as error:
            portal_access.log(request.env, 'submit', 'refused', record=order, detail='acknowledge')
            return self._back(order, error=str(error.args[0]))
        return self._back(order, done='acknowledged')

    @http.route('/my/procurement/orders/<int:order_id>/propose', type='http', auth='user',
                website=True, methods=['POST'])
    def spm_portal_propose(self, order_id, **post):
        """Propose a new delivery date, or two split dates, for one line of the order.

        Requirements: FR-SPM-0025
        """
        order, refusal_page = self._order_or_refusal(order_id, 'submit')
        if refusal_page:
            return refusal_page
        commitment = order.sudo().spm_commitment_ids.filtered(  # sudo: the line ids of an order the caller owns
            lambda c: str(c.id) == post.get('commitment_id'))
        if not commitment:
            return self._back(order, error='line')
        try:
            noon = commitment.order_line_id._convert_to_middle_of_day
            first, second = (noon(datetime.combine(day, time.min)) if day else None
                             for day in (self._day(post.get('date')), self._day(post.get('date_2'))))
            second_qty = float(post.get('quantity_2') or 0)
            if second and not first:
                raise ValueError('first date missing')
            if second:
                lines = delivery.split_lines(first, second, commitment.ordered_qty, second_qty)
                commitment.with_env(request.env).spm_propose(post.get('reason'), lines=lines)
            else:
                commitment.with_env(request.env).spm_propose(post.get('reason'), date=first)
        except (ValueError, TypeError):
            return self._back(order, error='date')
        except (UserError, AccessError, ValidationError) as error:
            portal_access.log(request.env, 'submit', 'refused', record=order, detail='propose')
            return self._back(order, error=str(error.args[0]))
        return self._back(order, done='proposed')

    @http.route()
    def portal_my_purchase_order_update_dates(self, order_id=None, access_token=None, **kw):
        """A supplier cannot write dates directly: a date change is a proposal the buyer decides.

        Requirements: FR-SPM-0025
        """
        if portal_access.is_supplier_user(request.env.user):
            if refusal_page := self._supplier_route_refusal('purchase.order', order_id, 'submit'):
                return refusal_page  # not the supplier's order: refused and logged as for any native route
            portal_access.log(request.env, 'submit', 'refused', res_model='purchase.order',
                              res_id=order_id, detail='direct date update')
            return self._refusal_page(403)
        return super().portal_my_purchase_order_update_dates(order_id=order_id, access_token=access_token, **kw)

    def _purchase_order_get_page_view_values(self, order, access_token, **kwargs):
        values = super()._purchase_order_get_page_view_values(order, access_token, **kwargs)
        if portal_access.is_supplier_user(request.env.user):
            commitments = order.sudo().spm_commitment_ids  # sudo: plain values of the supplier's own order lines
            values['spm_delivery'] = {
                'acknowledged': bool(order.sudo().spm_acknowledged_date),  # sudo: the acknowledgement flag of the own order
                'can_act': order.state == 'purchase',
                'rows': [{
                    'commitment': commitment,
                    'name': commitment.order_line_id.name,
                    'quantity': commitment.ordered_qty,
                    'date': commitment.agreed_date,
                    'pending': commitment.proposal_ids.filtered(lambda p: p.state == 'pending')[:1],
                    'open': commitment.state == 'open',
                } for commitment in commitments],
                'error': request.params.get('spm_error'),
                'done': request.params.get('spm_done'),
            }
        return values
