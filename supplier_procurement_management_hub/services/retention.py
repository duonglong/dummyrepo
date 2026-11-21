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
    """
    days = company.spm_portal_inactivity_days
    if days <= 0:
        return env['res.users']
    cutoff = (now or fields.Datetime.now()) - relativedelta(days=days)
    # sudo: the retention job reads the users of the company whatever the record rules of its user say
    users = env['res.users'].sudo().search([
        ('share', '=', True), ('group_ids', 'in', env.ref('base.group_portal').ids), ('company_id', '=', company.id)])
    return users.filtered(lambda user: (user.login_date or user.create_date) <= cutoff)


def purgeable_documents(env, company, now=None):
    """Return the supplier documents of ``company`` whose file is past the document retention period.

    Requirements: FR-SPM-0110
    Replaced versions count from their replaced date, current expired documents from their expiry date,
    rejected ones from their review date, documents of a rejected application from its last change.
    A period of 0 years switches the rule off. Only documents that still hold a file are returned.
    """
    years = company.spm_document_retention_years
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
    documents = env['procurement.supplier.document'].sudo().search(
        Domain('attachment_id', '!=', False) & due)
    company_ids = {company.id, False}
    return documents.filtered(
        lambda doc: (doc.partner_id.company_id.id or doc.application_id.company_id.id or False) in company_ids)
