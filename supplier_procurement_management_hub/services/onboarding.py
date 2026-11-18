"""Onboarding invitations and the field rules of an application (FR-SPM-0001..0003).

Plain functions: they take an environment or plain values and hold no state.
"""
import hashlib
import secrets
from datetime import timedelta

from odoo import fields

# A link works for 14 days (answer of the administrator, FR-SPM-0001); named once here.
INVITATION_VALID_DAYS = 14

# The link path, named once; the controller route and the e-mailed URL both build on it.
ONBOARDING_PATH = '/my/procurement/onboarding/'
# States in which the supplier may still fill in the application.
EDITABLE_STATES = ('invited', 'changes_requested')
# Keys that are always required (FR-SPM-0002): the supplier cannot be identified without them.
ALWAYS_REQUIRED_KEYS = ('legal_name', 'email')
# Fields of the keys a supplier never edits through the link.
PORTAL_READONLY_FIELDS = ('contact_ids', 'email')
# Fields only the module's own actions write (all run with elevated rights after the access check).
WORKFLOW_FIELDS = (
    'state', 'submitted_date', 'invitation_token_hash', 'invitation_expiry', 'invitation_used_date', 'partner_id',
    'change_request_comment', 'rejection_reason')
# States after which an application is read-only.
CLOSED_STATES = ('approved', 'rejected')

# Application fields behind each field key of a supplier type; the key says what is asked.
KEY_FIELDS = {
    'legal_name': ('legal_name',),
    'email': ('email',),
    'phone': ('phone',),
    'website': ('website',),
    'vat': ('vat',),
    'street': ('street', 'city', 'zip', 'country_id'),
    'bank': ('bank_account_number', 'bank_name', 'bank_bic', 'bank_holder'),
    'categories': ('product_category_ids',),
    'operating_locations': ('operating_locations',),
    'contacts': ('contact_ids',),
}
# Of a group of fields, the one that must be filled when the key is required.
KEY_REQUIRED_FIELD = {'street': 'street', 'bank': 'bank_account_number'}


def required_field(key):
    """Return the application field that must be filled when the field key ``key`` is required."""
    return KEY_REQUIRED_FIELD.get(key, KEY_FIELDS[key][0])


def new_token():
    """Return a fresh invitation token; only its hash is stored."""
    return secrets.token_urlsafe(32)


def hash_token(token):
    """Return the stored form of ``token``.

    Requirements: FR-SPM-0001
    """
    return hashlib.sha256(token.encode()).hexdigest()


def expiry_from_now():
    """Return the moment an invitation sent now expires."""
    return fields.Datetime.now() + timedelta(days=INVITATION_VALID_DAYS)


def link_state(application, now=None):
    """Return ``'ok'``, ``'used'`` or ``'expired'`` for the invitation of ``application``.

    Requirements: FR-SPM-0001
    """
    now = now or fields.Datetime.now()
    if application.invitation_used_date:
        return 'used'
    if not application.invitation_expiry or application.invitation_expiry < now:
        return 'expired'
    return 'ok'


# What a supplier may upload through the link (answer of the administrator, FR-SPM-0027).
UPLOAD_MAX_MB = 20
UPLOAD_MAX_BYTES = UPLOAD_MAX_MB * 1024 * 1024
UPLOAD_MIMETYPES = ('application/pdf', 'image/png', 'image/jpeg')


# --- review: approval into one supplier contact (FR-SPM-0005) -----------------------------

def normalized_tax_id(vat):
    """Return the tax identifier as compared for matching: stripped and lower-cased.

    Requirements: FR-SPM-0005
    """
    return (vat or '').strip().lower()


def lock_tax_id(env, vat):
    """Serialise the approvals of one tax identifier until the end of the transaction.

    Requirements: FR-SPM-0005
    """
    env.cr.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", ['spm_tax_id:' + normalized_tax_id(vat)])


def find_company_contact(env, vat):
    """Return the oldest active company contact of any company whose tax identifier matches ``vat``.

    Requirements: FR-SPM-0005

    Elevated read: a contact of another company is looked up by its tax identifier only, to
    link it instead of creating a second one; the caller has passed the role check.
    """
    tax_id = normalized_tax_id(vat)
    if not tax_id:
        return env['res.partner']
    env.cr.execute(
        "SELECT id FROM res_partner WHERE is_company AND active AND lower(btrim(vat)) = %s ORDER BY id LIMIT 1",
        [tax_id])
    return env['res.partner'].sudo().browse(  # elevated: see above
        [row[0] for row in env.cr.fetchall()])


def contact_values(application):
    """Return the contact fields copied from an application (read elevated: restricted fields).

    Requirements: FR-SPM-0005
    """
    application = application.sudo()  # elevated: restricted tax and bank fields, role checked by the caller
    return {
        'name': application.legal_name or application.email,
        'is_company': True,
        'company_id': False,
        'email': application.email,
        'vat': application.vat or False,
        'phone': application.phone,
        'website': application.website,
        'street': application.street,
        'zip': application.zip,
        'city': application.city,
        'country_id': application.country_id.id,
        'spm_supplier_type_id': application.supplier_type_id.id,
        'spm_product_category_ids': [(6, 0, application.product_category_ids.ids)],
    }


def approved_status(env):
    """Return the status configured for approved applications: the default of the Approved kind.

    Requirements: FR-SPM-0005
    """
    return env['procurement.supplier.status'].sudo().search(  # elevated: configuration read, role checked by the caller
        [('kind', '=', 'approved'), ('default_for_kind', '=', True)], limit=1)
