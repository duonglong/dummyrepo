"""Once-only notifications and the collect/process steps of the reminder jobs (ADR-SPM-007).

Requirements: BR-SPM-0019, FR-SPM-0091, FR-SPM-0092, FR-SPM-0093, FR-SPM-0094, FR-SPM-0095
Every notification goes through an Odoo mail template an Administrator can edit and translate; the key row
of ``procurement.notification.log`` (kind, record, recipient, due date) is inserted first, so a second run,
a crash or a manual run never sends the same notification twice.
"""
import logging
from datetime import timedelta

import pytz

from odoo import fields
from odoo.exceptions import UserError

from . import delivery, sourcing, supplier_control

_logger = logging.getLogger(__name__)

DOCUMENT_TEMPLATE = 'supplier_procurement_management_hub.mail_template_document_expiry'
QUOTE_REMINDER_TEMPLATE = 'supplier_procurement_management_hub.mail_template_quote_reminder'
QUOTE_MISSING_TEMPLATE = 'supplier_procurement_management_hub.mail_template_quote_missing'
DELIVERY_TEMPLATE = 'supplier_procurement_management_hub.mail_template_delivery_overdue'
CONTRACT_TEMPLATE = 'supplier_procurement_management_hub.mail_template_contract_reminder'


def claim(env, kind, record, partner, due_date):
    """Insert the key row of one notification; return True when it is new (the caller then sends).

    Requirements: BR-SPM-0019
    ``INSERT ... ON CONFLICT DO NOTHING`` so two runs at the same moment cannot both win. The key
    carries the company of the record (none for a shared record), so the company rule applies to it.
    """
    now = fields.Datetime.now()
    company = record.company_id if 'company_id' in record._fields else env['res.company']
    env.cr.execute(
        """INSERT INTO procurement_notification_log
               (kind, res_model, res_id, recipient_partner_id, due_date, company_id, sent_date,
                create_uid, create_date, write_uid, write_date)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (kind, res_model, res_id, recipient_partner_id, due_date) DO NOTHING
           RETURNING id""",
        [kind, record._name, record.id, partner.id, due_date, company.id or None, now,
         env.uid, now, env.uid, now])
    return bool(env.cr.fetchone())


def is_reachable(partner):
    """Return True when a notification can reach the partner: an e-mail address, or an inbox user."""
    return bool(partner.email) or any(user.notification_type == 'inbox' for user in partner.user_ids)


def report_unreached(record, done, missing):
    """Name the recipients a job could not reach: a warning when something else was done, else an error.

    Requirements: BR-SPM-0019
    Nothing is claimed for a recipient that cannot be reached, so a later run retries it. The error
    ends the record's savepoint, which the job run lists with the record.
    """
    if not missing:
        return
    message = record.env._("%(record)s: cannot notify %(who)s", record=record.display_name,
                           who=", ".join(missing))
    if done:
        _logger.warning(message)
        return
    raise UserError(message)


def company_day(env, company, moment):
    """Return the day of a UTC datetime in the time zone of the company.

    Requirements: NFR-SPM-0009
    """
    tz_name = company.sudo().partner_id.tz  # elevated: reads only the time zone
    if tz_name:
        moment = pytz.utc.localize(moment).astimezone(pytz.timezone(tz_name))
    return moment.date()


def send_once(env, kind, record, recipients, due_date, template, missing=None):
    """Notify each recipient partner once per kind, record and due date; return how many were sent.

    Requirements: BR-SPM-0019, FR-SPM-0095
    The text comes from the template in the language of the recipient, so an edited translation is used.
    The link of a supplier message leads to the supplier's own portal page only. The template sees the
    due date as ``ctx['spm_due_date']`` and the kind as ``ctx['spm_kind']``. A recipient that cannot be
    reached is not claimed and its name is added to ``missing``.
    """
    template = env.ref(template) if isinstance(template, str) else template
    template = template.with_context(spm_due_date=due_date, spm_kind=kind)
    sent = 0
    for partner in recipients:
        if not is_reachable(partner):
            if missing is not None:
                missing.append(partner.display_name)
        elif claim(env, kind, record, partner, due_date):
            _deliver(env, template, record, partner)
            sent += 1
    return sent


def _deliver(env, template, record, partner):
    """Render the template for the partner's language and post it as a notification of the record."""
    lang = partner.lang or False
    subject = template._render_field('subject', [record.id], set_lang=lang)[record.id]
    body = template._render_field(
        'body_html', [record.id], set_lang=lang, options={'post_process': True})[record.id]
    env['mail.thread'].message_notify(
        model=record._name, res_id=record.id, partner_ids=partner.ids, subject=subject, body=body)


def schedule_once(env, kind, record, user, due_date, summary, target=None, missing=None):
    """Give ``user`` one to-do per kind, ``record`` and due date; return True when it was created.

    Requirements: BR-SPM-0019
    The to-do sits on ``target`` (the record itself by default), so it can be keyed on a finer record
    than the one it is shown on. A missing user is added to ``missing``, nothing is claimed.
    """
    if not user:
        if missing is not None:
            missing.append(env._("nobody to give the to-do to"))
        return False
    if not claim(env, kind + '_activity', record, user.partner_id, due_date):
        return False
    (target or record).activity_schedule(
        'mail.mail_activity_data_todo', date_deadline=due_date, user_id=user.id, summary=summary)
    return True


def _supplier_partners(env, suppliers):
    return sourcing.portal_users(env, suppliers).partner_id


# --- supplier documents ---------------------------------------------------

def due_documents(env):
    """Return the current verified documents whose expiry date is within the reminder period or passed.

    Requirements: FR-SPM-0091
    """
    today = supplier_control.company_today(env)
    longest = max(env['procurement.document.type'].search([]).mapped('reminder_days'), default=0)
    horizon = fields.Date.today() + timedelta(days=longest + 1)  # a day of slack for the company's zone
    documents = env['procurement.supplier.document'].search([
        ('is_current', '=', True), ('verification_state', '=', 'verified'),
        ('expiry_date', '!=', False), ('expiry_date', '<=', horizon)], order='id')
    return documents.filtered(lambda doc: supplier_control.document_validity(
        doc.verification_state, doc.expiry_date, doc.document_type_id.reminder_days, today)
        in ('expiring', 'expired'))


def remind_document(env, document):
    """Notify the supplier's portal users and give its responsible buyer a to-do; return the mails sent.

    Requirements: FR-SPM-0091, BR-SPM-0019
    """
    supplier = document.partner_id.commercial_partner_id
    missing = []
    sent = send_once(env, 'document_expiry', document, _supplier_partners(env, supplier),
                     document.expiry_date, DOCUMENT_TEMPLATE, missing)
    scheduled = schedule_once(
        env, 'document_expiry', document, supplier.spm_responsible_user_id, document.expiry_date,
        env._("Renew %(document)s of %(supplier)s", document=document.document_type_id.name,
              supplier=supplier.name), target=supplier, missing=missing)
    report_unreached(document, sent or scheduled, missing)
    return sent


# --- quote deadlines --------------------------------------------------------

def open_events_waiting(env):
    """Return the open sourcing events with at least one invited supplier that has not answered.

    Requirements: FR-SPM-0092
    """
    return env['procurement.sourcing.event'].search(
        [('state', '=', 'open'), ('response_ids.state', '=', 'invited')], order='id')


def remind_event(env, event):
    """Remind the silent suppliers inside the reminder delay; after the deadline tell the buyer who is silent.

    Requirements: FR-SPM-0092, BR-SPM-0019
    """
    now = fields.Datetime.now()
    silent = event.response_ids.filtered(lambda response: response.state == 'invited')
    missing = []
    if event.deadline < now:
        sent = send_once(env, 'quote_missing', event, event.buyer_id.partner_id, event.deadline.date(),
                         QUOTE_MISSING_TEMPLATE, missing)
        report_unreached(event, sent, missing)
        return sent
    delay = timedelta(days=event.company_id.spm_quote_reminder_days)
    sent = 0
    for response in silent.filtered(lambda r: r.effective_deadline - now <= delay):
        sent += send_once(env, 'quote_reminder', event, _supplier_partners(env, response.partner_id),
                          response.effective_deadline.date(), QUOTE_REMINDER_TEMPLATE, missing)
    report_unreached(event, sent, missing)
    return sent


# --- deliveries --------------------------------------------------------------

def _late_items(env, commitment, delay):
    """Return the days of the schedules, or of the commitment itself, that are overdue by more than ``delay``.

    The late days are worked out here from today, not read from the stored-in-cache fields, so a run
    always sees its own day.
    """
    company = commitment.company_id
    today = supplier_control.company_today(env, company)
    rounding = commitment._spm_rounding()
    if commitment.schedule_ids:
        candidates = [(company_day(env, company, schedule.date), schedule.remaining_qty)
                      for schedule in commitment.schedule_ids]
    else:
        candidates = [(commitment.agreed_date and company_day(env, company, commitment.agreed_date),
                       commitment.remaining_qty)]
    return [day for day, remaining in candidates if delivery.overdue_days(day, today, remaining, rounding) > delay]


def overdue_commitments(env):
    """Return the open delivery commitments with a date before now and a quantity still to come.

    Requirements: FR-SPM-0093
    """
    now = fields.Datetime.now()
    return env['procurement.delivery.commitment'].search([
        ('state', '=', 'open'), ('remaining_qty', '>', 0),
        '|', ('agreed_date', '<', now), ('schedule_ids.date', '<', now)], order='id')


def escalate_commitment(env, commitment):
    """Give the order's buyer a to-do and notify the supplier's portal users, once per line and agreed date.

    Requirements: FR-SPM-0093, BR-SPM-0019
    """
    delay = commitment.company_id.spm_delivery_escalation_days
    order = commitment.order_id
    sent, missing, scheduled = 0, [], False
    for day in _late_items(env, commitment, delay):
        sent += send_once(env, 'delivery_overdue', commitment,
                          _supplier_partners(env, commitment.partner_id), day, DELIVERY_TEMPLATE, missing)
        scheduled |= schedule_once(
            env, 'delivery_overdue', commitment, order.user_id, day,
            env._("Chase %(product)s of %(order)s", product=commitment.product_id.display_name,
                  order=order.name), target=order, missing=missing)
    report_unreached(commitment, sent or scheduled, missing)
    return sent


# --- contracts ------------------------------------------------------------

def contracts_with_dates_ahead(env):
    """Return the active contracts whose end date, and so the notice date, has not passed.

    Requirements: FR-SPM-0094
    """
    yesterday = fields.Date.today() - timedelta(days=1)  # a day of slack for the company's zone
    return env['procurement.contract'].search(
        [('state', '=', 'active'), ('date_end', '>=', yesterday)], order='id')


def remind_contract(env, contract):
    """Notify the owner and give them a to-do due on the notice date and on the end date, inside the period.

    Requirements: FR-SPM-0094, BR-SPM-0019
    A notice date equal to the end date is reminded once, as the end date.
    """
    today = supplier_control.company_today(env, contract.company_id)
    dates = [('contract_end', contract.date_end, env._("%s ends", contract.display_name))]
    if contract.notice_date and contract.notice_date != contract.date_end:
        dates.insert(0, ('contract_notice', contract.notice_date,
                         env._("Notice date of %s", contract.display_name)))
    sent, missing, scheduled = 0, [], False
    for kind, day, summary in dates:
        if 0 <= (day - today).days <= contract.reminder_days:
            sent += send_once(env, kind, contract, contract.owner_id.partner_id, day, CONTRACT_TEMPLATE,
                              missing)
            scheduled |= schedule_once(env, kind, contract, contract.owner_id, day, summary, missing=missing)
    report_unreached(contract, sent or scheduled, missing)
    return sent
