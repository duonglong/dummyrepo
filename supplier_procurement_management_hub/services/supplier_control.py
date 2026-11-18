"""Supplier document validity and compliance (FR-SPM-0007, FR-SPM-0008).

Plain functions: they take an environment or plain values and hold no state.
"""
from datetime import timedelta

import pytz

from odoo import fields

# Worst first: the supplier shows the worst state among its required documents.
COMPLIANCE_ORDER = ('expired_or_missing', 'rejected', 'to_verify', 'expiring', 'valid')
# Best first: of several current documents of one type the best one counts.
VALIDITY_ORDER = ('valid', 'expiring', 'to_verify', 'rejected', 'expired')


def company_today(env, company=None):
    """Return today's date in the time zone of the company.

    Requirements: FR-SPM-0008
    """
    company = company or env.company
    tz_name = company.sudo().partner_id.tz  # elevated: reads only the time zone
    now = fields.Datetime.now()
    if tz_name:
        now = pytz.utc.localize(now).astimezone(pytz.timezone(tz_name))
    return now.date()


def document_validity(verification_state, expiry_date, reminder_days, today):
    """Return the validity state of one document.

    Requirements: FR-SPM-0008
    Rejected first; a document whose expiry date is before today is expired;
    then not yet verified; then expiring when the expiry date is within the
    reminder period (inclusive); else valid.
    """
    if verification_state == 'rejected':
        return 'rejected'
    if expiry_date and expiry_date < today:
        return 'expired'
    if verification_state != 'verified':
        return 'to_verify'
    if expiry_date and expiry_date <= today + timedelta(days=reminder_days):
        return 'expiring'
    return 'valid'


def compliance_rows(env, partner):
    """Return ``[(document type, state)]`` for each required type of the supplier.

    Requirements: FR-SPM-0008
    State is one of valid, expiring, to_verify, rejected, expired or missing.
    """
    required = partner.spm_supplier_type_id.required_document_type_ids
    if not required:
        return []
    documents = env['procurement.supplier.document'].search([
        ('partner_id', '=', partner.id), ('is_current', '=', True),
        ('document_type_id', 'in', required.ids)])
    rows = []
    for document_type in required:
        states = [document.validity_state for document in documents
                  if document.document_type_id == document_type]
        state = min(states, key=VALIDITY_ORDER.index) if states else 'missing'
        rows.append((document_type, state))
    return rows


def compliance_state(rows):
    """Return the worst state of the rows (or False when nothing is required).

    Requirements: FR-SPM-0008
    """
    if not rows:
        return False
    states = {'expired_or_missing' if state in ('expired', 'missing') else state
              for _document_type, state in rows}
    return next(state for state in COMPLIANCE_ORDER if state in states)
