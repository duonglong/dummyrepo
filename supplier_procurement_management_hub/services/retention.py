"""Retention of idle supplier portal accounts and of old supplier files.

Requirements: FR-SPM-0110
"""
from dateutil.relativedelta import relativedelta

from odoo import fields
from odoo.fields import Domain


def idle_portal_users(env, company, now=None):
    """Return the active portal users of ``company`` with no login for its inactivity period.

    Requirements: FR-SPM-0110
    A period of 0 days switches the rule off. A user who never logged in counts from its creation.
    Users of a known customer (customer rank, no supplier signal) are left alone: only supplier accounts go.
    """
    days = company.spm_portal_inactivity_days
    if days <= 0:
        return env['res.users']
    cutoff = (now or fields.Datetime.now()) - relativedelta(days=days)
    # sudo: the retention job reads the users of the company whatever the record rules of its user say
    users = env['res.users'].sudo().search([
        ('share', '=', True), ('group_ids', 'in', env.ref('base.group_portal').ids), ('company_id', '=', company.id)])
    return users.filtered(
        lambda user: (user.login_date or user.create_date) <= cutoff and not _is_customer_only(user))


def _is_customer_only(user):
    """Tell whether the commercial contact of ``user`` is a customer with no supplier signal."""
    commercial = user.partner_id.commercial_partner_id
    return commercial.customer_rank > 0 and not commercial.spm_is_supplier


def shared_retention_years(env):
    """Return the retention period of documents that belong to no company, in years (0 = off).

    Requirements: FR-SPM-0110
    The longest period of the companies applies, so no company loses a file it still keeps;
    one company with the rule off keeps the files of shared suppliers.
    """
    periods = env['res.company'].sudo().search([]).mapped('spm_document_retention_years')
    return 0 if not periods or min(periods) <= 0 else max(periods)


def _company_domain(company):
    """Return the domain of the documents of ``company``; an empty recordset means the documents of no company."""
    if not company:
        return Domain('partner_id.company_id', '=', False) & Domain.OR([
            [('application_id', '=', False)], [('application_id.company_id', '=', False)]])
    return Domain.OR([
        [('partner_id.company_id', '=', company.id)],
        [('partner_id.company_id', '=', False), ('application_id.company_id', '=', company.id)],
    ])


def purgeable_documents(env, company=None, now=None):
    """Return the supplier documents of ``company`` whose file is past the document retention period.

    Requirements: FR-SPM-0110
    Without a company, the documents that belong to none (shared suppliers) with the shared period.
    Replaced versions count from their replaced date, current expired documents from their expiry date,
    rejected ones from their review date, documents of a rejected application from its last change.
    A period of 0 years switches the rule off. Only documents that still hold a file are returned.
    """
    years = company.spm_document_retention_years if company else shared_retention_years(env)
    if years <= 0:
        return env['procurement.supplier.document']
    cutoff = (now or fields.Datetime.now()) - relativedelta(years=years)
    due = Domain.OR([
        [('is_current', '=', False), ('replaced_date', '<=', cutoff)],
        [('is_current', '=', True), ('expiry_date', '<', cutoff.date())],
        [('is_current', '=', True), ('verification_state', '=', 'rejected'), ('review_date', '<=', cutoff)],
        [('is_current', '=', True), ('application_id.state', '=', 'rejected'),
         ('application_id.write_date', '<=', cutoff)],
    ])
    # sudo: the retention job reads the files of every supplier whatever the record rules of its user say
    return env['procurement.supplier.document'].sudo().search(
        Domain('attachment_id', '!=', False) & due & _company_domain(company or env['res.company']))
