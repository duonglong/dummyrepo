"""Supplier document validity and compliance (FR-SPM-0007, FR-SPM-0008).

Plain functions: they take an environment or plain values and hold no state.
"""
from datetime import timedelta

import pytz

from odoo import fields
from odoo.exceptions import UserError
from odoo.tools import html2plaintext

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


# Status kinds a supplier may have to take part in a controlled flow (BR-SPM-0006).
CONTROL_ALLOWED_KINDS = ('approved', 'conditional')
# Models whose records a controlled flow acts on (an override can name only these).
CONTROLLED_MODELS = ('purchase.order',)
# The override event of each override kind in the audit trail.
OVERRIDE_AUDIT_EVENTS = {'status': 'status_override', 'risk': 'risk_override'}


def has_override(env, record, partner, action, kind):
    """Return True when a manager recorded an override of this kind for this one record.

    Requirements: FR-SPM-0013, FR-SPM-0090
    Elevated rights (read of ``procurement.override``): the buyer who acts has no
    access to the overrides; the search is limited to the one record, supplier,
    action and kind.
    """
    return bool(env['procurement.override'].sudo().search_count([
        ('res_model', '=', record._name), ('res_id', '=', record.id),
        ('company_id', '=', record.company_id.id),
        ('partner_id', '=', partner.id), ('action', '=', action),
        ('kind', '=', kind)], limit=1))


def has_status_override(env, record, partner, action):
    """Return True when a manager recorded a status override for this one record.

    Requirements: FR-SPM-0013
    """
    return has_override(env, record, partner, action, 'status')


def controlled_record(env, res_model, res_id):
    """Return the record an override names, read as the acting user.

    Requirements: FR-SPM-0013, BR-SPM-0001
    Refuses a model that no controlled flow uses, a record that does not exist
    and a record the user cannot read (for example one of another company).
    """
    if res_model not in CONTROLLED_MODELS:
        raise UserError(env._("An override cannot be recorded for this kind of record."))
    record = env[res_model].browse(res_id).exists()
    if not record:
        raise UserError(env._("The record of the override does not exist."))
    record.check_access('read')
    return record


def _status_control(company):
    """Return True when the company controls the supplier status (BR-SPM-0006)."""
    return company.sudo().spm_supplier_control  # elevated: company setting read


def _already_noted(record, message):
    """Return True when the record's chatter already holds this note.

    Elevated rights (read of the record's messages): the acting user may not read
    the chatter; only the text of the notes is compared.
    """
    return any(html2plaintext(note.body or '').strip() == message
               for note in record.sudo().message_ids)  # elevated: chatter read


def _status_refusal(env, record, partner, action):
    """Return the refusal message of a supplier status that is not allowed, or False.

    Requirements: BR-SPM-0006, FR-SPM-0013
    """
    if partner.spm_status_kind in CONTROL_ALLOWED_KINDS or has_status_override(env, record, partner, action):
        return False
    status = partner.spm_status_id.display_name or env._("no status")
    return env._(
        "Supplier %(supplier)s cannot be used here: its status is %(status)s. "
        "A Procurement manager can record an override with a reason.",
        supplier=partner.display_name, status=status)


class RiskFinding:
    """A risk level that restricts a controlled flow: a refusal or a warning.

    Requirements: FR-SPM-0090
    """

    def __init__(self, blocking, message):
        self.blocking = blocking
        self.message = message

    def __str__(self):
        return self.message


def _risk_finding(env, partner, company):
    """Return the finding of the supplier's risk level in this company, or None.

    Requirements: FR-SPM-0090
    Elevated rights (read of the restricted risk level): a buyer who acts cannot
    read the field; only the level and the company's configured reaction are read.
    """
    level = partner.sudo().spm_risk_level  # elevated: restricted field, only the level is read
    action = level and company.sudo()['spm_risk_%s_action' % level]  # elevated: company setting read
    if action not in ('warn', 'block'):
        return None
    label = dict(partner._fields['spm_risk_level']._description_selection(env))[level]
    if action == 'warn':
        return RiskFinding(False, env._(
            "Supplier %(supplier)s has a %(level)s risk level.", supplier=partner.display_name, level=label))
    return RiskFinding(True, env._(
        "Supplier %(supplier)s cannot be used here: its risk level is %(level)s. "
        "A Procurement manager can record an override with a reason.",
        supplier=partner.display_name, level=label))


def check(env, partners, action, company, record):
    """Return the risk findings (refusals and warnings) of the suppliers for this action.

    Requirements: FR-SPM-0090
    The company's per-level risk settings decide, whether or not it controls supplier
    status; a refusal covered by a risk override of this record is left out.
    """
    findings = []
    for partner in partners.commercial_partner_id:
        finding = _risk_finding(env, partner, company)
        if finding and finding.blocking and has_override(env, record, partner, action, 'risk'):
            continue
        if finding:
            findings.append(finding)
    return findings


def refusal_kinds(env, record, partner, company, action):
    """Return the override kinds ('status', 'risk') that this record still needs to go on.

    Requirements: FR-SPM-0013, FR-SPM-0090
    The status counts only in a company with supplier control; the risk level always.
    """
    partner = partner.commercial_partner_id
    kinds = ['status'] if _status_control(company) and _status_refusal(env, record, partner, action) else []
    if any(finding.blocking for finding in check(env, partner, action, company, record)):
        kinds.append('risk')
    return kinds


def check_supplier_allowed(env, record, partner, company, action):
    """Refuse a controlled flow with a supplier whose status or risk level is not allowed.

    Requirements: BR-SPM-0006, FR-SPM-0013, FR-SPM-0090
    A status other than Approved or Conditional (only in a company with supplier
    control) and a risk level configured as blocking are refused unless an override
    of that kind exists for this record; a level configured as warning is noted
    once on the record and goes on. The messages name the supplier and its status
    or risk level.
    """
    partner = partner.commercial_partner_id
    refusal = _status_control(company) and _status_refusal(env, record, partner, action)
    if refusal:
        raise UserError(refusal)
    for finding in check(env, partner, action, company, record):
        if finding.blocking:
            raise UserError(finding.message)
        if hasattr(record, 'message_post') and not _already_noted(record, finding.message):
            record.message_post(body=finding.message, subtype_xmlid='mail.mt_note')
