"""Once-only notifications and the collect/process steps of the reminder jobs (ADR-SPM-007).

Requirements: BR-SPM-0019, FR-SPM-0091, FR-SPM-0092, FR-SPM-0093, FR-SPM-0094, FR-SPM-0095
Every notification goes through an Odoo mail template an Administrator can edit and translate; the key row
of ``procurement.notification.log`` (kind, record, recipient, due date) is inserted first, so a second run,
a crash or a manual run never sends the same notification twice.
"""
from datetime import timedelta

from odoo import fields

from . import delivery, sourcing

DOCUMENT_TEMPLATE = 'supplier_procurement_management_hub.mail_template_document_expiry'
QUOTE_REMINDER_TEMPLATE = 'supplier_procurement_management_hub.mail_template_quote_reminder'
QUOTE_MISSING_TEMPLATE = 'supplier_procurement_management_hub.mail_template_quote_missing'
DELIVERY_TEMPLATE = 'supplier_procurement_management_hub.mail_template_delivery_overdue'
CONTRACT_TEMPLATE = 'supplier_procurement_management_hub.mail_template_contract_reminder'


def claim(env, kind, record, partner, due_date):
    """Insert the key row of one notification; return True when it is new (the caller then sends).

    Requirements: BR-SPM-0019
    ``INSERT ... ON CONFLICT DO NOTHING`` so two runs at the same moment cannot both win.
    """
    now = fields.Datetime.now()
    env.cr.execute(
        """INSERT INTO procurement_notification_log
               (kind, res_model, res_id, recipient_partner_id, due_date,
                create_uid, create_date, write_uid, write_date)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (kind, res_model, res_id, recipient_partner_id, due_date) DO NOTHING
           RETURNING id""",
        [kind, record._name, record.id, partner.id, due_date, env.uid, now, env.uid, now])
    return bool(env.cr.fetchone())


def send_once(env, kind, record, recipients, due_date, template):
    """Notify each recipient partner once per kind, record and due date; return how many were sent.

    Requirements: BR-SPM-0019, FR-SPM-0095
    The text comes from the template in the language of the recipient, so an edited translation is used.
    The link of a supplier message leads to the supplier's own portal page only.
    """
    template = env.ref(template) if isinstance(template, str) else template
    sent = 0
    for partner in recipients:
        if claim(env, kind, record, partner, due_date):
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


def schedule_once(env, kind, record, user, due_date, summary):
    """Give ``user`` one to-do on ``record`` per kind and due date; return True when it was created.

    Requirements: BR-SPM-0019
    """
    if not user or not claim(env, kind + '_activity', record, user.partner_id, due_date):
        return False
    record.activity_schedule(
        'mail.mail_activity_data_todo', date_deadline=due_date, user_id=user.id, summary=summary)
    return True


def _supplier_partners(env, suppliers):
    return sourcing.portal_users(env, suppliers).partner_id


# --- supplier documents ---------------------------------------------------

def due_documents(env):
    """Return the current verified documents whose expiry date is within the reminder period or passed.

    Requirements: FR-SPM-0091
    """
    today = fields.Date.context_today(env['procurement.job'])
    documents = env['procurement.supplier.document'].search([
        ('is_current', '=', True), ('verification_state', '=', 'verified'),
        ('expiry_date', '!=', False)], order='id')
    return documents.filtered(
        lambda doc: doc.expiry_date <= today + timedelta(days=doc.document_type_id.reminder_days))


def remind_document(env, document):
    """Notify the supplier's portal users and give its responsible buyer a to-do; return the mails sent.

    Requirements: FR-SPM-0091, BR-SPM-0019
    """
    supplier = document.partner_id.commercial_partner_id
    sent = send_once(env, 'document_expiry', document, _supplier_partners(env, supplier),
                     document.expiry_date, DOCUMENT_TEMPLATE)
    schedule_once(env, 'document_expiry', supplier, supplier.spm_responsible_user_id, document.expiry_date,
                  env._("Renew %(document)s of %(supplier)s", document=document.document_type_id.name,
                        supplier=supplier.name))
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
    if event.deadline < now:
        return send_once(env, 'quote_missing', event, event.buyer_id.partner_id, event.deadline.date(),
                         QUOTE_MISSING_TEMPLATE)
    delay = timedelta(days=event.company_id.spm_quote_reminder_days)
    sent = 0
    for response in silent.filtered(lambda r: r.effective_deadline - now <= delay):
        sent += send_once(env, 'quote_reminder', event, _supplier_partners(env, response.partner_id),
                          response.effective_deadline.date(), QUOTE_REMINDER_TEMPLATE)
    return sent


# --- deliveries --------------------------------------------------------------

def _late_items(env, commitment, delay):
    """Return the days of the schedules, or of the commitment itself, that are overdue by more than ``delay``.

    The late days are worked out here from today, not read from the stored-in-cache fields, so a run
    always sees its own day.
    """
    holder = env['procurement.job']
    today = fields.Date.context_today(holder)
    rounding = commitment._spm_rounding()
    if commitment.schedule_ids:
        candidates = [(fields.Date.context_today(holder, schedule.date), schedule.remaining_qty)
                      for schedule in commitment.schedule_ids]
    else:
        candidates = [(commitment.agreed_date and fields.Date.context_today(holder, commitment.agreed_date),
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
    sent = 0
    for day in _late_items(env, commitment, delay):
        sent += send_once(env, 'delivery_overdue', commitment,
                          _supplier_partners(env, commitment.partner_id), day, DELIVERY_TEMPLATE)
        schedule_once(env, 'delivery_overdue', order, order.user_id, day,
                      env._("Chase %(product)s of %(order)s", product=commitment.product_id.display_name,
                            order=order.name))
    return sent


# --- contracts ------------------------------------------------------------

def contracts_with_dates_ahead(env):
    """Return the active contracts whose end date, and so the notice date, has not passed.

    Requirements: FR-SPM-0094
    """
    today = fields.Date.context_today(env['procurement.job'])
    return env['procurement.contract'].search(
        [('state', '=', 'active'), ('date_end', '>=', today)], order='id')


def remind_contract(env, contract):
    """Notify the owner and give them a to-do due on the notice date and on the end date, inside the period.

    Requirements: FR-SPM-0094, BR-SPM-0019
    A notice date equal to the end date is reminded once, as the end date.
    """
    today = fields.Date.context_today(env['procurement.job'])
    dates = [('contract_end', contract.date_end, env._("%s ends", contract.display_name))]
    if contract.notice_date and contract.notice_date != contract.date_end:
        dates.insert(0, ('contract_notice', contract.notice_date,
                         env._("Notice date of %s", contract.display_name)))
    sent = 0
    for kind, day, summary in dates:
        if 0 <= (day - today).days <= contract.reminder_days:
            sent += send_once(env, kind, contract, contract.owner_id.partner_id, day, CONTRACT_TEMPLATE)
            schedule_once(env, kind, contract, contract.owner_id, day, summary)
    return sent
