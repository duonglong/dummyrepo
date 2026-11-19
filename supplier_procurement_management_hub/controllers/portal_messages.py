from odoo import http
from odoo.exceptions import AccessError, UserError
from odoo.http import request

from ..services import portal_access
from .portal import SupplierPortal


class SupplierPortalMessages(SupplierPortal):
    """The question a supplier asks on a quote request it is invited to.

    Requirements: FR-SPM-0023, BR-SPM-0003
    The supplier is the session user's company contact; the event id of the address is checked
    by the model against the user's own responses.
    """

    @http.route('/my/procurement/rfq/<int:event_id>/question', type='http', auth='user',
                website=True, methods=['POST'])
    def spm_portal_rfq_question(self, event_id, **post):
        """Record the question and send the supplier back to the page it came from.

        Requirements: FR-SPM-0023
        """
        refusal_page = self._supplier_list_refusal()
        if refusal_page:
            return refusal_page
        try:
            request.env['procurement.sourcing.question'].spm_ask(event_id, post.get('question'))
        except AccessError:
            portal_access.log(request.env, 'denied', 'refused', res_model='procurement.sourcing.event',
                              res_id=event_id, detail='not found')
            return self._refusal_page(404)
        except UserError as error:
            return request.redirect('/my/procurement?error=%s' % error.args[0])
        return request.redirect(request.httprequest.referrer or '/my/procurement')
