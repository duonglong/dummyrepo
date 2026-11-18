"""Who a supplier portal user is, what they own, and the portal activity log.

Requirements: BR-SPM-0002, FR-SPM-0017, FR-SPM-0027, FR-SPM-0028
"""

# The portal's page size (DESIGN section 6).
PORTAL_PAGE_SIZE = 20
# Orders a supplier sees: never a draft the buyer has not sent.
VISIBLE_ORDER_STATES = ('sent', 'purchase', 'cancel')


class SupplierAccessRefused(Exception):
    """The portal user may not use the supplier portal; ``reason`` is the logged detail."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def is_supplier_user(user):
    """True for a portal user whose company contact is a supplier of the hub.

    Requirements: BR-SPM-0002
    """
    if user._is_public() or not user.share:
        return False
    return bool(user.sudo().partner_id.commercial_partner_id.spm_is_supplier)  # sudo: reads the company contact flag only


def current_supplier(env):
    """Return the supplier (company contact) of the logged-in portal user or refuse.

    Requirements: BR-SPM-0002, FR-SPM-0028

    The supplier comes from the session user, never from the request; an archived user,
    a user of no supplier or a supplier whose status kind is Blocked is refused.
    """
    user = env.user
    if user._is_public() or not user.active or not user.share:
        raise SupplierAccessRefused('revoked')
    supplier = user.sudo().partner_id.commercial_partner_id  # sudo: the company contact of the session user
    if not supplier.spm_is_supplier:
        raise SupplierAccessRefused('not a supplier')
    if supplier.spm_status_kind == 'blocked':
        raise SupplierAccessRefused('blocked')
    return supplier


def refusal(env):
    """The refusal of the session user as an exception, or None when the user may use the portal.

    Requirements: FR-SPM-0028
    """
    try:
        current_supplier(env)
    except SupplierAccessRefused as refused:
        return refused
    return None


def get_owned(env, model, record_id, domain=None):
    """Return the record when the user may read it (record rules apply), else an empty recordset.

    Requirements: BR-SPM-0002, FR-SPM-0017

    The id comes from the address: it is searched as the caller, so the rules of the record decide,
    and a missing record and a foreign one look the same.
    """
    if not isinstance(record_id, int) or record_id <= 0:
        return env[model]
    return env[model].search([('id', '=', record_id)] + list(domain or []), limit=1)


def log(env, action, result, supplier=None, record=None, res_model=None, res_id=None, detail=''):
    """Write one portal activity entry for the session user.

    Requirements: FR-SPM-0027
    """
    if record:
        res_model, res_id = record._name, record.id
    # sudo: a portal user has no create right on the log; one entry, values set here
    company_contact = env.user.sudo().partner_id.commercial_partner_id  # sudo: the session user's company contact
    env['procurement.portal.log'].sudo().create({  # sudo: see above
        'user_id': env.user.id,
        'partner_id': supplier.id if supplier else (company_contact.id or False),
        'action': action,
        'res_model': res_model or False,
        'res_id': res_id or False,
        'result': result,
        'detail': detail,
    })


def order_rows(env, orders):
    """One row per order: its receipt status and its posted bills with their payment status.

    Requirements: FR-SPM-0016, BR-SPM-0003

    The orders are already the user's own; the bill ids are collected elevated, then the bills are
    read once as the user, so the native rule on the user's own posted vendor bills decides.
    """
    receipt_labels = dict(env['purchase.order']._fields['receipt_status']._description_selection(env))
    payment_labels = dict(env['account.move']._fields['payment_state']._description_selection(env))
    order_bill_ids = {order.id: set(order.sudo().invoice_ids.ids) for order in orders}  # sudo: ids only
    bills = env['account.move'].search([
        ('id', 'in', list(set().union(*order_bill_ids.values()))),
        ('state', '=', 'posted'), ('move_type', '=', 'in_invoice')])
    return [{
        'order': order,
        'receipt': receipt_labels.get(order.receipt_status, ''),
        'bills': [{
            'name': bill.name,
            'payment': payment_labels.get(bill.payment_state, ''),
        } for bill in bills.filtered(lambda bill: bill.id in order_bill_ids[order.id])],
    } for order in orders]
