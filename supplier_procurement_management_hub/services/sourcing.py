"""Sourcing rules: merging requests into event lines, inviting suppliers, notifying them.

Requirements: FR-SPM-0031, FR-SPM-0032, FR-SPM-0033, FR-SPM-0042
Plain functions on an environment, called by the models (and later by the portal).
"""
from odoo.exceptions import UserError

from . import supplier_control

INVITATION_TEMPLATE = 'supplier_procurement_management_hub.mail_template_sourcing_invitation'
DEADLINE_TEMPLATE = 'supplier_procurement_management_hub.mail_template_sourcing_deadline'
CANCEL_TEMPLATE = 'supplier_procurement_management_hub.mail_template_sourcing_cancelled'


def merge_request_lines(requests):
    """Return the event line values of the requests, one line per product and unit.

    Requirements: FR-SPM-0031
    Request lines without a product are merged by description and unit. The quantities add up
    and every merged request line is linked to the event line it went into.
    """
    merged = {}
    for line in requests.line_ids:
        key = (line.product_id.id, line.uom_id.id, '' if line.product_id else line.name)
        values = merged.setdefault(key, {
            'product_id': line.product_id.id, 'name': line.name, 'uom_id': line.uom_id.id,
            'quantity': 0.0, 'request_line_ids': []})
        values['quantity'] += line.quantity
        values['request_line_ids'].append((4, line.id))
    return list(merged.values())


def check_invitable(env, event):
    """Refuse to send an event without a supplier, or with a supplier the control refuses.

    Requirements: FR-SPM-0032, FR-SPM-0013
    """
    if not event.invited_partner_ids:
        raise UserError(env._(
            "At least one supplier must be invited before sending: add suppliers in the Invited suppliers field."))
    for partner in event.invited_partner_ids:
        supplier_control.check_supplier_allowed(env, event, partner, event.company_id, 'invite')


def portal_users(env, partners):
    """Return the active portal users of the suppliers' company contacts.

    Requirements: FR-SPM-0032
    """
    return env['res.users'].search([
        ('share', '=', True), ('active', '=', True),
        ('partner_id.commercial_partner_id', 'in', partners.commercial_partner_id.ids)])


def notify_suppliers(env, event, template_xmlid, partners):
    """E-mail each portal user of the suppliers separately with the template of the event.

    Requirements: FR-SPM-0032, FR-SPM-0033, FR-SPM-0042
    One mail per user, so a supplier's users never see each other's address.
    """
    template = env.ref(template_xmlid)
    for user in portal_users(env, partners):
        template.send_mail(event.id, force_send=False, email_values={
            'recipient_ids': [(6, 0, user.partner_id.ids)], 'email_to': False})
