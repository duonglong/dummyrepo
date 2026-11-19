"""Sourcing rules: merging requests into event lines, inviting suppliers, notifying them.

Requirements: FR-SPM-0020, FR-SPM-0021, FR-SPM-0022, FR-SPM-0031, FR-SPM-0032, FR-SPM-0033,
FR-SPM-0034, FR-SPM-0035, FR-SPM-0036, FR-SPM-0037, FR-SPM-0042, BR-SPM-0007
Plain functions on an environment, called by the models (and later by the portal).
"""
import math

from odoo import fields
from odoo.exceptions import UserError

from . import onboarding, supplier_control

INVITATION_TEMPLATE = 'supplier_procurement_management_hub.mail_template_sourcing_invitation'
DEADLINE_TEMPLATE = 'supplier_procurement_management_hub.mail_template_sourcing_deadline'
CANCEL_TEMPLATE = 'supplier_procurement_management_hub.mail_template_sourcing_cancelled'
REVISION_TEMPLATE = 'supplier_procurement_management_hub.mail_template_sourcing_revision'


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


TERM_FIELDS = ('payment_term_id', 'payment_days', 'validity_date', 'warranty_months', 'notes')
LINE_KEYS = ('event_line_id', 'price_unit', 'discount', 'lead_time_days', 'tax_ids')
DISCOUNT_MAX = 100.0


def check_accepts_quotation(env, response, by_buyer):
    """Refuse a quotation on an event that is not open, or after the supplier's deadline.

    Requirements: FR-SPM-0022, FR-SPM-0042
    The deadline is the supplier's own when the Buyer reopened it, and the server time decides.
    A quotation the Buyer enters is not held to the deadline.
    """
    event = response.event_id.sudo()  # sudo: the state and deadline of an event the user is invited to
    if event.state != 'open':
        raise UserError(env._("The sourcing event %s is not open for quotations.", event.name))
    deadline = response.effective_deadline
    if not by_buyer and fields.Datetime.now() > deadline:
        raise UserError(env._(
            "The deadline %(deadline)s (UTC) has passed: the quotation is no longer accepted.",
            deadline=fields.Datetime.to_string(deadline)))


def _number(env, value, label, integer=False):
    """A finite number from a submitted value, or a UserError naming the field."""
    try:
        number = float(value if value not in (None, False, '') else 0)
    except (TypeError, ValueError):
        number = math.nan
    if not math.isfinite(number) or number < 0 or (integer and number != int(number)):
        raise UserError(env._("%s must be a number of zero or more.", label))
    return int(number) if integer else number


def check_purchase_taxes(env, event, tax_ids):
    """Refuse a tax that is not a purchase tax of the event's company (one search for all lines)."""
    # sudo: only to count the purchase taxes of the event's company the offers name
    if tax_ids and env['account.tax'].sudo().search_count([
            ('id', 'in', list(tax_ids)), *env['account.tax']._check_company_domain(event.company_id),
            ('type_tax_use', '=', 'purchase')]) != len(tax_ids):
        raise UserError(env._("A tax of the quotation is not a purchase tax of the company."))


def prepare_lines(env, response, lines):
    """The creation values of the version lines; only lines the event asked for, each once.

    Requirements: FR-SPM-0020
    """
    event = response.event_id.sudo()  # sudo: the lines the buyer asked for, an invited supplier reads no event
    asked = {line.id: line for line in event.line_ids}
    seen = set()
    values = []
    taxes_by_line = []
    for offer in lines or []:
        event_line = asked.get(offer.get('event_line_id'))
        if not event_line:
            raise UserError(env._("A quotation can only answer the lines the buyer asked for."))
        if event_line.id in seen:
            raise UserError(env._("The line %s is quoted twice.", event_line.name))
        seen.add(event_line.id)
        discount = _number(env, offer.get('discount'), env._("The discount"))
        if discount > DISCOUNT_MAX:
            raise UserError(env._("The discount is between 0 and 100 percent."))
        tax_ids = list(offer.get('tax_ids') or [])
        taxes_by_line.append(tax_ids)
        values.append({
            'event_line_id': event_line.id, 'quantity': event_line.quantity, 'uom_id': event_line.uom_id.id,
            'price_unit': _number(env, offer.get('price_unit'), env._("The price")),
            'discount': discount,
            'lead_time_days': _number(env, offer.get('lead_time_days'), env._("The lead time"), integer=True),
            'tax_ids': [(6, 0, tax_ids)]})
    if not values:
        raise UserError(env._("Quote at least one line."))
    check_purchase_taxes(env, event, set().union(*taxes_by_line))
    return values


def prepare_terms(env, terms):
    """The commercial terms of a version; a payment term is searched with the user's own rights."""
    unknown = set(terms) - set(TERM_FIELDS)
    if unknown:
        raise UserError(env._("Unknown quotation terms: %s.", ', '.join(sorted(unknown))))
    values = {'notes': terms['notes']} if terms.get('notes') else {}
    if terms.get('validity_date'):
        try:
            values['validity_date'] = fields.Date.to_date(terms['validity_date'])
        except ValueError:
            raise UserError(env._("The validity date is not a valid date.")) from None
    for key, label in (('payment_days', env._("The payment days")), ('warranty_months', env._("The warranty"))):
        values[key] = _number(env, terms.get(key), label, integer=True)
    term_id = terms.get('payment_term_id')
    if term_id:
        if not env['account.payment.term'].search_count([('id', '=', term_id)]):
            raise UserError(env._("The payment terms are not available."))
        values['payment_term_id'] = term_id
    return values


ENTRY_MODEL = 'procurement.quotation.entry'


def bind_attachments(env, attachment_ids):
    """The files of a quotation: they exist, the user made them, and they belong to nothing yet.

    Requirements: FR-SPM-0020, FR-SPM-0034, NFR-SPM-0003
    A file the buyer's dialog uploaded belongs to the dialog's model and counts as unattached.
    A supplier's file keeps to the portal's size and type limits, whatever way it was made.
    """
    ids = list(dict.fromkeys(attachment_ids or []))
    if not all(isinstance(attachment_id, int) and attachment_id > 0 for attachment_id in ids):
        raise UserError(env._("A file of the quotation is not available."))
    attachments = env['ir.attachment'].browse(ids).exists()
    # sudo: reads who made each file and what it is attached to, to refuse a file of someone else
    unfit = attachments.sudo().filtered(
        lambda a: a.create_uid != env.user or (a.res_model or a.res_id) and a.res_model != ENTRY_MODEL)
    if len(attachments) != len(ids) or unfit:
        raise UserError(env._("A file of the quotation is not available."))
    # sudo: reads the content of the user's own files to check their size and type
    if env.user.share and not all(
            a.file_size <= onboarding.UPLOAD_MAX_BYTES and onboarding.is_allowed_upload(a.raw.content)
            for a in attachments.sudo()):
        raise UserError(onboarding.upload_refusal_text(env))
    return attachments


def add_version(response, lines, attachment_ids, terms, by_buyer):
    """Store a new version of the response, the current one, keeping every earlier version.

    Requirements: FR-SPM-0020, FR-SPM-0021, FR-SPM-0022, FR-SPM-0034

    Every check runs before anything is written. The version is numbered under a lock of the
    response, so two submissions at once get two numbers.
    """
    env = response.env
    check_accepts_quotation(env, response, by_buyer)
    line_values = prepare_lines(env, response, lines)
    term_values = prepare_terms(env, terms)
    attachments = bind_attachments(env, attachment_ids)
    env.flush_all()
    env.cr.execute('SELECT id FROM procurement_sourcing_response WHERE id = %s FOR UPDATE', [response.id])
    env.invalidate_all()
    check_accepts_quotation(env, response, by_buyer)
    # sudo: a portal user and a buyer have no create right on versions; the checks are above
    versions = env['procurement.sourcing.response.version'].sudo()
    previous = versions.search([('response_id', '=', response.id)])
    version = versions.with_context(spm_new_version=True).create({
        'response_id': response.id,
        'version': max(previous.mapped('version'), default=0) + 1,
        'submitted_date': fields.Datetime.now(),
        'submitted_by_id': env.user.id,
        'entered_by_buyer': by_buyer,
        'is_current': True,
        'currency_id': response.event_id.sudo().currency_id.id,  # sudo: the event's currency
        'line_ids': [(0, 0, line) for line in line_values],
        'attachment_ids': [(6, 0, attachments.ids)],
        **term_values})
    previous.filtered('is_current').write({'is_current': False})
    # sudo: the files are the user's own and unbound (checked above); a token never opens them
    attachments = attachments.sudo()
    attachments.write({'access_token': False})
    attachments.write({'res_model': version._name, 'res_id': version.id})
    # sudo: the state and the current version are workflow fields no user writes directly
    response.sudo().write({'state': 'submitted', 'current_version_id': version.id})
    return version


def request_revision(response, note_to_supplier, new_deadline, internal_note=None):
    """Reopen one supplier's response until a new deadline, keep the note and tell the supplier.

    Requirements: FR-SPM-0036
    The Buyer needs the right to change the response; the event must be open and the new
    deadline later than now and than the event's own deadline (the later one counts).
    """
    env = response.env
    response.check_access('write')
    note = (note_to_supplier or '').strip()
    if not note:
        raise UserError(env._("A note to the supplier is required to request a revised quotation."))
    new_deadline = fields.Datetime.to_datetime(new_deadline)
    event = response.event_id
    if event.state != 'open':
        raise UserError(env._("The sourcing event %s is not open for quotations.", event.name))
    if not new_deadline or new_deadline <= max(fields.Datetime.now(), event.deadline):
        raise UserError(env._("The new deadline must be later than now and than the deadline of the event."))
    # sudo: a buyer only reads the history; the row is written here after the checks above
    negotiation = env['procurement.sourcing.negotiation'].sudo().create({
        'event_id': event.id, 'response_id': response.id, 'note_to_supplier': note,
        'internal_note': (internal_note or '').strip() or False,
        'new_deadline': new_deadline, 'user_id': env.user.id})
    # sudo: the state is a workflow field no user writes directly; the checks are above
    response.sudo().write({'state': 'reopened', 'reopened_deadline': new_deadline})
    notify_suppliers(env, negotiation, REVISION_TEMPLATE, response.partner_id)
    return negotiation


def _unit_price(rline):
    """The price of one unit of an offered line after its discount."""
    return rline.price_unit * (1 - rline.discount / 100.0)


def _normalized_amount(event, rline, amount, version, uom_to=None):
    """An amount of the offered line in the event's currency, on its tax basis and unit.

    Requirements: FR-SPM-0037, BR-SPM-0007
    ``uom_to`` converts a price per offered unit to a price per comparison unit.
    """
    if uom_to and rline.uom_id:
        amount = rline.uom_id._compute_price(amount, uom_to)
    taxes = rline.tax_ids.compute_all(amount, currency=version.currency_id, quantity=1.0)
    amount = taxes['total_included' if event.tax_basis == 'included' else 'total_excluded']
    return version.currency_id._convert(
        amount, event.currency_id, event.company_id, event.rate_date, round=False)


def comparison_assumptions(env, event):
    """The text naming how the values of a comparison were made comparable.

    Requirements: FR-SPM-0037, BR-SPM-0007
    """
    if not event.is_normalized:
        return env._(
            "Values are not normalized: the event does not state its comparison currency, rate "
            "date, tax basis and the comparison unit of every line, so raw values are shown "
            "and price is left out of the weighted score.")
    basis = env._("with tax") if event.tax_basis == 'included' else env._("without tax")
    units = ', '.join(f"{line.name}: {line.comparison_uom_id.name}" for line in event.line_ids)
    return env._(
        "Normalized in %(currency)s at the rate of %(date)s, prices %(basis)s. Comparison units: %(units)s.",
        currency=event.currency_id.name, date=fields.Date.to_string(event.rate_date),
        basis=basis, units=units)


def _criterion_value(criterion, version, total, lines):
    """The raw value of a criterion for one quotation version."""
    if criterion.kind == 'price':
        return total
    if criterion.kind == 'total_cost':
        return version.amount_total
    if criterion.kind == 'lead_time':
        return max((line.lead_time_days for line in lines), default=0)
    if criterion.kind == 'terms':
        return version.payment_days
    return 0.0


def _cells_of_response(env, event, response, normalized):
    """The cells of one response: its lines, its criteria and its total.

    Requirements: FR-SPM-0035, FR-SPM-0037
    """
    version = response.current_version_id
    quoted = {line.event_line_id: line for line in version.line_ids}
    cells = []
    total = total_normalized = 0.0
    for event_line in event.line_ids:
        rline = quoted.get(event_line)
        base = {'row_kind': 'line', 'event_line_id': event_line.id, 'response_id': response.id}
        if not rline:
            cells.append(dict(base, missing=True))
            continue
        price = _unit_price(rline)
        cells.append(dict(
            base, raw_value=price, currency=version.currency_id.id, uom=rline.uom_id.id,
            raw_display=f"{price:.2f} {version.currency_id.name} / {rline.uom_id.name or event_line.uom_id.name}",
            normalized_value=_normalized_amount(
                event, rline, price, version, event_line.comparison_uom_id) if normalized else 0.0))
        total += rline.price_subtotal
        if normalized:
            total_normalized += _normalized_amount(event, rline, rline.price_subtotal, version)
    cells.append({
        'row_kind': 'total', 'response_id': response.id, 'raw_value': total,
        'currency': version.currency_id.id, 'missing': len(quoted) < len(event.line_ids),
        'raw_display': f"{total:.2f} {version.currency_id.name}", 'normalized_value': total_normalized})
    for criterion in event.criterion_ids:
        value = _criterion_value(criterion, version, total, version.line_ids)
        money = criterion.kind in ('price', 'total_cost')
        cells.append({
            'row_kind': 'criterion', 'criterion_id': criterion.id, 'response_id': response.id,
            'raw_value': value, 'raw_display': f"{value:g}",
            'currency': version.currency_id.id if money else False,
            'normalized_value': total_normalized if normalized and criterion.kind == 'price' else 0.0})
    return cells


def _mark_best(event, cells, normalized):
    """Mark the best cell of each row: the lowest price, total and lead time, or the criterion's direction.

    Requirements: FR-SPM-0035
    Cells of an offer that left a line out never win a total; raw prices in several
    currencies are not comparable, so none of them is marked.
    """
    criteria = {criterion.id: criterion for criterion in event.criterion_ids}
    rows = {}
    for cell in cells:
        if cell.get('missing') or cell['row_kind'] == 'criterion' and (
                criteria[cell['criterion_id']].kind == 'manual'):
            continue
        key = (cell['row_kind'], cell.get('event_line_id'), cell.get('criterion_id'))
        rows.setdefault(key, []).append(cell)
    for (kind, _line, criterion_id), row in rows.items():
        higher = kind == 'criterion' and criteria[criterion_id].direction == 'higher'
        money = kind != 'criterion' or row[0]['currency']
        use_normalized = normalized and money
        if money and not normalized and len({cell['currency'] for cell in row}) > 1:
            continue
        values = [cell['normalized_value'] if use_normalized else cell['raw_value'] for cell in row]
        best = max(values) if higher else min(values)
        for cell, value in zip(row, values):
            cell['is_best'] = value == best


def compare(event):
    """Rebuild the stored comparison of the current quotations of the event and return it.

    Requirements: FR-SPM-0035, FR-SPM-0037, BR-SPM-0007
    One cell per line, criterion and total of every response that has a current version,
    read and written with the user's own rights. Manual scores and comments are kept.
    """
    env = event.env
    event.check_access('read')
    normalized = event.is_normalized
    responses = event.response_ids.filtered('current_version_id')
    cells = []
    for response in responses:
        cells.extend(_cells_of_response(env, event, response, normalized))
    _mark_best(event, cells, normalized)
    comparison = env['procurement.comparison'].search([('event_id', '=', event.id)])
    kept = {(cell.criterion_id.id, cell.response_id.id): (cell.manual_score, cell.manual_comment)
            for cell in comparison.line_ids if cell.row_kind == 'criterion'
            and (cell.manual_score or cell.manual_comment)}
    values = {
        'assumptions': comparison_assumptions(env, event), 'is_normalized': normalized,
        'computed_at': fields.Datetime.now(), 'line_ids': [(5, 0, 0)]}
    if comparison:
        comparison.write(values)
    else:
        comparison = env['procurement.comparison'].create(dict(values, event_id=event.id))
    rows = []
    for cell in cells:
        cell.pop('currency', None)
        cell.pop('uom', None)
        manual = kept.get((cell.get('criterion_id'), cell['response_id']))
        if manual:
            cell['manual_score'], cell['manual_comment'] = manual
        rows.append(dict(cell, comparison_id=comparison.id))
    env['procurement.comparison.line'].create(rows)
    return comparison
