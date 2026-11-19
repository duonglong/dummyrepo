"""Who a supplier portal user is, what they own, and the portal activity log.

Requirements: BR-SPM-0002, FR-SPM-0017, FR-SPM-0027, FR-SPM-0028
The quotation page, form and file rules (FR-SPM-0017, FR-SPM-0020, FR-SPM-0027) are at the end.
The portal profile and upload rules (FR-SPM-0015, FR-SPM-0018, FR-SPM-0019, NFR-SPM-0003) follow.
"""
import base64
import os

from odoo import fields
from odoo.addons.base.models.res_partner_bank import sanitize_account_number
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools import mimetypes

from . import onboarding

# The longest text a profile field takes from the portal.
PROFILE_TEXT_MAX = 1000
BANK_FIELDS = {
    'bank_account_number': 'account_number', 'bank_name': 'bank_name',
    'bank_bic': 'bank_bic', 'bank_holder': 'holder_name'}
UPLOAD_KEYS = ('document_type_id', 'issue_date', 'expiry_date', 'file')
BOOLEAN_TRUE = ('1', 'true', 'on', 'yes')
BOOLEAN_FALSE = ('0', 'false', 'off', 'no')

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


def is_procurement_record(env, model_name, record_id):
    """True when the record is one the supplier checks guard: an order, a vendor bill, a hub record,
    or an attachment of one of them. Customer invoices, sales and shared links keep Odoo's own rules.

    Requirements: BR-SPM-0002, FR-SPM-0028
    """
    if model_name == 'purchase.order' or model_name.startswith('procurement.'):
        return True
    if model_name == 'account.move':
        move = env['account.move'].sudo().browse(record_id).exists()  # sudo: the move type only
        # a missing id is answered as a foreign bill would be
        return not move or move.move_type in ('in_invoice', 'in_refund', 'in_receipt')
    if model_name == 'ir.attachment':
        attachment = env['ir.attachment'].sudo().browse(record_id).exists()  # sudo: its owner only
        return bool(attachment.res_model and attachment.res_id) and attachment.res_model != model_name \
            and is_procurement_record(env, attachment.res_model, attachment.res_id)
    return False


def check_document(env, model_name, record_id, action):
    """Return the record the session user owns and log the access; refuse and log otherwise.

    Requirements: BR-SPM-0002, FR-SPM-0017, FR-SPM-0027, FR-SPM-0028

    The one rule of every portal path: the refusal of the user first, then ownership by the
    user's own rights (a token is never looked at), then one activity entry.
    """
    try:
        supplier = current_supplier(env)
    except SupplierAccessRefused as refused:
        log(env, 'denied', 'refused', res_model=model_name, res_id=record_id, detail=refused.reason)
        raise
    domain = [('state', 'in', VISIBLE_ORDER_STATES)] if model_name == 'purchase.order' else None
    record = get_owned(env, model_name, record_id, domain)
    if not record:
        log(env, 'denied', 'refused', supplier=supplier, res_model=model_name, res_id=record_id,
            detail='not found')
        raise SupplierAccessRefused('not found')
    log(env, action, 'ok', supplier=supplier, record=record)
    return record


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


def quote_rows(env, responses):
    """One row per quote request the supplier is invited to: number, title, deadline and state.

    Requirements: FR-SPM-0016, FR-SPM-0032
    The responses are already the user's own (portal rule); the event is read elevated for
    these four plain values only, as the portal user has no right on the event record.
    """
    state_labels = dict(env['procurement.sourcing.event']._fields['state']._description_selection(env))
    return [{
        'response': response,
        'event_id': event.id,
        'name': event.name,
        'title': event.title,
        'deadline': event.deadline,
        'state': state_labels.get(event.state, ''),
    } for response in responses for event in response.event_id.sudo()]  # sudo: four plain values of an invited event


def editable_fields(env):
    """The contact fields the administrator marked editable in the portal, as ``name: field``.

    Requirements: FR-SPM-0018
    """
    # sudo: the field list is configuration a portal user cannot read; it holds names only
    rows = env['procurement.portal.field'].sudo().search([('editable', '=', True)])
    partner_fields = env['res.partner']._fields
    return {row.field_id.name: partner_fields[row.field_id.name]
            for row in rows if row.field_id.name in partner_fields}


def parse_field_value(env, field, raw):
    """The value of a submitted profile field, or a ValueError with the refusal text.

    Requirements: FR-SPM-0018
    """
    raw = (raw or '').strip()
    label = field._description_string(env)
    if len(raw) > PROFILE_TEXT_MAX:
        raise ValueError(env._("%s is too long.", label))
    if not raw:
        return False
    try:
        if field.type == 'boolean':
            if raw.lower() not in BOOLEAN_TRUE + BOOLEAN_FALSE:
                raise ValueError(raw)
            return raw.lower() in BOOLEAN_TRUE
        if field.type == 'integer':
            return int(raw)
        if field.type == 'float':
            return float(raw)
        if field.type == 'date':
            return fields.Date.to_date(raw) or False
        if field.type == 'selection':
            if raw not in dict(field._description_selection(env)):
                raise ValueError(raw)
    except (ValueError, TypeError):
        raise ValueError(env._("%s has an invalid value.", label)) from None
    return raw


def bank_details(post):
    """The bank details of a submitted profile, keyed by the account fields; empty without an account number."""
    details = {target: (post.get(source) or '').strip() for source, target in BANK_FIELDS.items()}
    return details if details['account_number'] else {}


def plain_account(number):
    """An account number as the native unique constraint reads it, to tell whether two are the same."""
    return sanitize_account_number(number) or ''


def _bank_is_known(supplier, details):
    """Whether an active account of the supplier already has this number and every given detail.

    Requirements: FR-SPM-0015

    A same number with another holder, bank or BIC is a change; so is an archived number.
    """
    number = plain_account(details['account_number'])
    for bank in supplier.sudo().bank_ids:  # sudo: compare only
        if plain_account(bank.account_number) != number:
            continue
        same_holder = not details['holder_name'] or details['holder_name'] == (bank.holder_name or '')
        same_bank = not details['bank_name'] or details['bank_name'] == (bank.bank_id.name or '')
        same_bic = not details['bank_bic'] or details['bank_bic'] == (bank.bank_id.bic or '')
        if same_holder and same_bank and same_bic:
            return True
    return False


def log_unknown_keys(env, supplier, post, known, route):
    """Log the names of the posted keys the route does not know; they are ignored.

    Requirements: FR-SPM-0018
    """
    unknown = sorted(key for key in post if key not in known and key != 'csrf_token')
    if unknown:
        log(env, 'submit', 'ok', supplier=supplier, detail='%s ignored keys: %s' % (route, ', '.join(unknown)))


def update_profile(env, supplier, post):
    """Write the editable fields of the post and hold a bank change; return the refusal texts.

    Requirements: FR-SPM-0015, FR-SPM-0018

    Only the fields marked editable are looked at; every other key of the post is ignored.
    A bank change never touches the supplier's accounts: it waits as a pending change.
    """
    errors = []
    values = {}
    editable = editable_fields(env)
    log_unknown_keys(env, supplier, post, set(editable) | set(BANK_FIELDS), 'profile')
    for name, field in editable.items():
        if name not in post:
            continue
        try:
            value = parse_field_value(env, field, post[name])
        except ValueError as error:
            errors.append(str(error))
            continue
        if value != supplier.sudo()[name] and (value or supplier.sudo()[name]):  # sudo: plain values of the own company
            values[name] = value
    if errors:
        return errors
    if values:
        try:
            with env.cr.savepoint():
                supplier._spm_portal_write(values)
        except (UserError, ValidationError) as error:
            return [str(error)]
    details = bank_details(post)
    if details and not _bank_is_known(supplier, details):
        change = env['procurement.supplier.change']._spm_hold_bank_change(supplier, details)
        log(env, 'submit', 'ok', supplier=supplier, record=change, detail='bank change pending')
    log(env, 'submit', 'ok', supplier=supplier, detail='profile')
    return []


upload_refusal_text = onboarding.upload_refusal_text


def read_upload(env, upload):
    """Return ``(data, mimetype)`` of an uploaded file, or raise ValueError with the refusal text.

    Requirements: FR-SPM-0019, NFR-SPM-0003

    The size is capped while reading and the type is the one of the content, never the extension.
    """
    data = upload.read(onboarding.UPLOAD_MAX_BYTES + 1) if upload else b''
    mimetype = mimetypes.guess_mimetype(data) if data else ''
    if not data or len(data) > onboarding.UPLOAD_MAX_BYTES or mimetype not in onboarding.UPLOAD_MIMETYPES:
        raise ValueError(upload_refusal_text(env))
    return data, mimetype


def upload_document(env, supplier, post, upload):
    """Store a portal upload as a To verify document and keep the replaced one as an earlier version.

    Requirements: FR-SPM-0019, NFR-SPM-0003

    Raises ValueError with the refusal text. The document type is searched with the user's own rights.
    """
    log_unknown_keys(env, supplier, post, UPLOAD_KEYS, 'upload')
    document_type = get_owned(env, 'procurement.document.type', int(post['document_type_id'])
                              if str(post.get('document_type_id', '')).isdigit() else 0)
    if not document_type:
        raise ValueError(env._("Choose a document type from the list."))
    try:
        issue_date = fields.Date.to_date(post.get('issue_date') or None)
        expiry_date = fields.Date.to_date(post.get('expiry_date') or None)
    except ValueError:
        raise ValueError(env._("A date is not valid.")) from None
    if document_type.needs_expiry and not expiry_date:
        raise ValueError(env._("The expiry date is required for this document."))
    data, mimetype = read_upload(env, upload)
    # sudo: a portal user may only read documents; one new document and its file, for the checked supplier
    documents = env['procurement.supplier.document'].sudo()
    previous = documents.search([
        ('partner_id', '=', supplier.id), ('document_type_id', '=', document_type.id),
        ('is_current', '=', True)], order='id desc')
    try:
        with env.cr.savepoint():
            document = documents.create({
                'partner_id': supplier.id, 'document_type_id': document_type.id,
                'issue_date': issue_date, 'expiry_date': expiry_date,
                'verification_state': 'to_verify', 'uploaded_from_portal': True,
                'previous_version_id': previous[:1].id})
            attachment = env['ir.attachment'].sudo().create({  # sudo: the file of the document just created
                'name': (os.path.basename(upload.filename) or 'file')[:255],
                'datas': base64.b64encode(data), 'mimetype': mimetype,
                'res_model': document._name, 'res_id': document.id})
            document.attachment_id = attachment
            previous.write({'is_current': False, 'replaced_date': fields.Datetime.now()})
    except ValidationError as error:
        raise ValueError(str(error)) from None
    log(env, 'submit', 'ok', supplier=supplier, record=document, detail='upload')
    return document


RESPONSE_MODEL = 'procurement.sourcing.response'
VERSION_MODEL = 'procurement.sourcing.response.version'
EVENT_MODEL = 'procurement.sourcing.event'
QUOTE_TERM_KEYS = ('validity_date', 'warranty_months', 'payment_days', 'notes')


def check_own_response(env, response):
    """Refuse a response that is not the session user's own supplier's, or a user who may not use the portal.

    Requirements: BR-SPM-0002, FR-SPM-0020, FR-SPM-0028
    """
    try:
        supplier = current_supplier(env)
    except SupplierAccessRefused as refused:
        raise AccessError(env._("You no longer have access to the supplier portal.")) from refused
    response.check_access('read')
    if response.partner_id != supplier:
        raise AccessError(env._("This quotation is not yours."))


def owned_response(env, event_id):
    """Return ``(supplier, response)`` of the session user for the event of the address, or refuse and log.

    Requirements: BR-SPM-0002, FR-SPM-0017, FR-SPM-0028

    The event is never read by the user: the user's own response to it (portal rule) is the proof
    of the invitation, and an event of someone else looks like one that does not exist.
    """
    try:
        supplier = current_supplier(env)
    except SupplierAccessRefused as refused:
        log(env, 'denied', 'refused', res_model=EVENT_MODEL, res_id=event_id, detail=refused.reason)
        raise
    response = env[RESPONSE_MODEL].search(
        [('event_id', '=', event_id), ('partner_id', '=', supplier.id)], limit=1)
    if not response:
        log(env, 'denied', 'refused', supplier=supplier, res_model=EVENT_MODEL, res_id=event_id,
            detail='not found')
        raise SupplierAccessRefused('not found')
    return supplier, response


def event_files(env, event):
    """The files the buyer attached to the event itself, never those of its message thread.

    Requirements: BR-SPM-0003, FR-SPM-0017
    A file of an internal note or of an e-mailed reply belongs to a message; only a file that
    belongs to no message is the buyer's own attachment to the event.
    """
    # sudo: the files of an event the user is invited to, and the files its messages carry
    thread_files = env['mail.message'].sudo().search([
        ('model', '=', EVENT_MODEL), ('res_id', '=', event.id)]).attachment_ids
    return env['ir.attachment'].sudo().search([
        ('res_model', '=', EVENT_MODEL), ('res_id', '=', event.id), ('id', 'not in', thread_files.ids)])


def purchase_taxes(env, event):
    """Plain ``{id, name}`` of the purchase taxes of the event's company, for the form's tax choice."""
    # sudo: the purchase taxes of the event's company, offered as plain values to an invited supplier
    taxes = env['account.tax'].sudo().search([
        *env['account.tax']._check_company_domain(event.company_id), ('type_tax_use', '=', 'purchase')])
    return [{'id': tax.id, 'name': tax.name} for tax in taxes]


def quote_page_values(env, response):
    """Plain values of the quotation page: the event's lines and files and the supplier's own versions.

    Requirements: FR-SPM-0020, FR-SPM-0021, FR-SPM-0022
    """
    event = response.event_id.sudo()  # sudo: plain values of an event the user is invited to
    current = response.current_version_id
    offered = {line.event_line_id.id: line for line in current.line_ids}
    files = event_files(env, event)
    offered_taxes = {line.event_line_id.id: line.tax_ids.ids for line in current.line_ids}
    return {
        'event': {'id': event.id, 'name': event.name, 'title': event.title, 'terms': event.terms,
                  'currency': event.currency_id.name},
        'currency': event.currency_id,
        'taxes': purchase_taxes(env, event),
        'deadline': response.effective_deadline,
        'accepting': event.state == 'open' and fields.Datetime.now() <= response.effective_deadline,
        'lines': [{
            'id': line.id, 'name': line.name, 'quantity': line.quantity, 'uom': line.uom_id.name,
            'price_unit': offered[line.id].price_unit if line.id in offered else '',
            'discount': offered[line.id].discount if line.id in offered else '',
            'lead_time_days': offered[line.id].lead_time_days if line.id in offered else '',
            'tax_id': (offered_taxes[line.id] or [''])[0] if line.id in offered else '',
        } for line in event.line_ids],
        'files': [{'id': attachment.id, 'name': attachment.name} for attachment in files],
        'versions': [{
            'version': version.version, 'date': version.submitted_date, 'total': version.amount_untaxed,
            'current': version.is_current, 'by_buyer': version.entered_by_buyer,
        } for version in response.version_ids],
    }


def parse_quotation_post(env, response, post):
    """The ``(lines, terms)`` of a submitted quotation form; the fields of lines left empty are not quoted.

    Requirements: FR-SPM-0020

    The keys are price_unit_<line id>, discount_<line id>, lead_time_<line id> and tax_<line id>;
    the ids are only checked against the lines of the event when the version is stored.
    """
    lines = []
    for key, price in post.items():
        if not key.startswith('price_unit_') or not str(price).strip():
            continue
        line_id = key[len('price_unit_'):]
        if not line_id.isdigit():
            continue
        tax = str(post.get('tax_' + line_id) or '').strip()
        lines.append({
            'event_line_id': int(line_id), 'price_unit': str(price).strip().replace(',', '.'),
            'discount': str(post.get('discount_' + line_id) or 0).strip().replace(',', '.'),
            'lead_time_days': post.get('lead_time_' + line_id) or 0,
            'tax_ids': [int(tax)] if tax.isdigit() else []})
    terms = {key: post[key].strip() for key in QUOTE_TERM_KEYS if str(post.get(key) or '').strip()}
    return lines, terms


def create_quote_attachments(env, uploads):
    """Create the checked files of a quotation form as the user's own unbound attachments.

    Requirements: FR-SPM-0020, NFR-SPM-0003
    Raises ValueError with the refusal text.
    """
    attachments = env['ir.attachment']
    for upload in uploads:
        if not upload or not upload.filename:
            continue
        data, mimetype = read_upload(env, upload)
        attachments |= env['ir.attachment'].sudo().create({  # sudo: a portal user's own file, made after the checks
            'name': (os.path.basename(upload.filename) or 'file')[:255],
            'raw': data, 'mimetype': mimetype})
    return attachments


def check_quote_file(env, attachment_id):
    """Return the file of an invited event or of the user's own quotation, and log the download.

    Requirements: BR-SPM-0002, FR-SPM-0017, FR-SPM-0027, FR-SPM-0028

    A token is never looked at. Anything else answers not found and is logged as denied.
    """
    try:
        supplier = current_supplier(env)
    except SupplierAccessRefused as refused:
        log(env, 'denied', 'refused', res_model='ir.attachment', res_id=attachment_id, detail=refused.reason)
        raise
    # sudo: reads the owner of the file only; the access is decided below
    attachment = env['ir.attachment'].sudo().browse(
        attachment_id if isinstance(attachment_id, int) and attachment_id > 0 else 0).exists()
    allowed = False
    if attachment.res_model == EVENT_MODEL:
        invited = env[RESPONSE_MODEL].search(
            [('event_id', '=', attachment.res_id), ('partner_id', '=', supplier.id)], limit=1)
        allowed = bool(invited) and attachment in event_files(env, invited.event_id.sudo())
    elif attachment.res_model == VERSION_MODEL:
        allowed = bool(get_owned(env, VERSION_MODEL, attachment.res_id))
    if not allowed:
        log(env, 'denied', 'refused', supplier=supplier, res_model='ir.attachment', res_id=attachment_id,
            detail='not found')
        raise SupplierAccessRefused('not found')
    log(env, 'download', 'ok', supplier=supplier, res_model=attachment.res_model, res_id=attachment.res_id)
    return attachment
