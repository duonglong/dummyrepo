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
    'state', 'submitted_date', 'invitation_token_hash', 'invitation_expiry', 'invitation_used_date', 'partner_id')
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
