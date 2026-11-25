"""Sourcing rules: merging requests into event lines, inviting suppliers, notifying them.

Requirements: FR-SPM-0020, FR-SPM-0021, FR-SPM-0022, FR-SPM-0031, FR-SPM-0032, FR-SPM-0033,
FR-SPM-0034, FR-SPM-0035, FR-SPM-0036, FR-SPM-0037, FR-SPM-0038, FR-SPM-0039, FR-SPM-0040,
FR-SPM-0041, FR-SPM-0042, BR-SPM-0007, BR-SPM-0008
Plain functions on an environment, called by the models (and later by the portal).
"""
import math
from datetime import timedelta

from odoo import fields
from odoo.exceptions import UserError

from . import audit, onboarding, report, supplier_control

INVITATION_TEMPLATE = 'supplier_procurement_management_hub.mail_template_sourcing_invitation'
DEADLINE_TEMPLATE = 'supplier_procurement_management_hub.mail_template_sourcing_deadline'
CANCEL_TEMPLATE = 'supplier_procurement_management_hub.mail_template_sourcing_cancelled'
AWARDED_OTHER_TEMPLATE = 'supplier_procurement_management_hub.mail_template_sourcing_awarded_other'
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

    Requirements: FR-SPM-0032, FR-SPM-0013, FR-SPM-0040
    Every line needs a product, so that its award can always become a purchase order line.
    """
    without_product = event.line_ids.filtered(lambda line: not line.product_id)
    if without_product:
        raise UserError(env._(
            "Choose a product for every line before sending: %s.", ', '.join(without_product.mapped('name'))))
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


def _normalized_amount(event, rline, amount, version, rate, uom_to=None, basis=None):
    """An amount of the offered line in the event's currency, on its tax basis and unit.

    Requirements: FR-SPM-0037, BR-SPM-0007
    ``uom_to`` converts a price per offered unit to a price per comparison unit; ``rate`` is the
    conversion rate of the version's currency at the event's rate date, looked up once per response.
    """
    if uom_to and rline.uom_id:
        amount = rline.uom_id._compute_price(amount, uom_to)
    taxes = rline.tax_ids.compute_all(amount, currency=version.currency_id, quantity=1.0)
    included = (basis or event.tax_basis) == 'included'
    return taxes['total_included' if included else 'total_excluded'] * rate


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


def _payment_days(version):
    """The payment days of a version: the stated days, else the longest day count of its payment term."""
    return version.payment_days or max(version.payment_term_id.line_ids.mapped('nb_days'), default=0)


def _term_given(version):
    """Whether the offer states its payment terms, as days or as a payment term (immediate payment counts)."""
    return bool(version.payment_days or version.payment_term_id)


def _criterion_value(criterion, version, total, lines):
    """The raw value of a criterion for one quotation version."""
    if criterion.kind == 'price':
        return total
    if criterion.kind == 'total_cost':
        return version.amount_total
    if criterion.kind == 'lead_time':
        return max((line.lead_time_days for line in lines), default=0)
    if criterion.kind == 'terms':
        return _payment_days(version)
    return 0.0


# Rows built from the quotation terms whatever the criteria: key -> 'higher' is better (None: no best).
TERM_ROWS = {
    'total_cost': False, 'lead_time': False, 'payment_terms': None, 'warranty': True, 'validity': True}
# Rows that add up or take the maximum over the lines: an offer that left a line out never wins them.
AGGREGATE_KINDS = ('price', 'total_cost', 'lead_time')
# Criteria whose value is an integer the supplier may leave blank: a stored 0 means not given, so missing.
BLANK_ZERO_KINDS = ('lead_time', 'terms')
# Criteria whose score is given by the buyer on the cell, not read from the quotation.
GIVEN_KINDS = ('manual', 'yes_no')


def _term_cell(version, key, total_cost, normalized_cost, incomplete):
    """The cell of one fixed term row of a response (total cost, lead time, terms, warranty, validity).

    A lead time, payment days, warranty or validity left blank is stored as 0 or empty: not given, so missing.
    """
    currency = version.currency_id
    cell = {'row_kind': 'term', 'term_key': key, 'raw_display': '', 'rankable': True}
    if key == 'total_cost':
        cell.update(raw_value=total_cost, currency=currency.id, normalized_value=normalized_cost or 0.0,
                    has_normalized=normalized_cost is not None,
                    raw_display=f"{total_cost:.{currency.decimal_places}f} {currency.name}")
    elif key == 'lead_time':
        days = max((line.lead_time_days for line in version.line_ids), default=0)
        cell.update(raw_value=days, raw_display=f"{days}", rankable=bool(days))
    elif key == 'payment_terms':
        cell.update(raw_value=_payment_days(version),
                    raw_display=version.payment_term_id.name or f"{version.payment_days}",
                    rankable=_term_given(version))
    elif key == 'warranty':
        cell.update(raw_value=version.warranty_months, raw_display=f"{version.warranty_months}",
                    rankable=bool(version.warranty_months))
    else:
        cell.update(raw_value=version.validity_date.toordinal() if version.validity_date else 0,
                    raw_display=fields.Date.to_string(version.validity_date) if version.validity_date else '',
                    rankable=bool(version.validity_date))
    cell['missing'] = incomplete and key in AGGREGATE_KINDS or not cell['rankable']
    return cell


def _cells_of_response(env, event, response, normalized):
    """The cells of one response: its lines, its total, its term rows and its criteria.

    Requirements: FR-SPM-0035, FR-SPM-0037
    Cells that aggregate over the lines of an offer that left a line out carry ``missing``.
    """
    version = response.current_version_id
    quoted = {line.event_line_id: line for line in version.line_ids}
    incomplete = len(quoted) < len(event.line_ids)
    currency = version.currency_id
    rate = currency._get_conversion_rate(
        currency, event.currency_id, event.company_id, event.rate_date) if normalized else 1.0
    cells = []
    total = total_normalized = cost_normalized = 0.0
    for event_line in event.line_ids:
        rline = quoted.get(event_line)
        base = {'row_kind': 'line', 'event_line_id': event_line.id, 'response_id': response.id}
        if not rline:
            cells.append(dict(base, missing=True, raw_display=env._("Missing")))
            continue
        price = _unit_price(rline)
        cells.append(dict(
            base, raw_value=price, currency=currency.id, uom=rline.uom_id.id,
            raw_display=f"{price:.{currency.decimal_places}f} {currency.name} / "
                        f"{rline.uom_id.name or event_line.uom_id.name}",
            has_normalized=normalized,
            normalized_value=_normalized_amount(
                event, rline, price, version, rate, event_line.comparison_uom_id) if normalized else 0.0))
        total += rline.price_subtotal
        if normalized:
            total_normalized += _normalized_amount(event, rline, rline.price_subtotal, version, rate)
            cost_normalized += _normalized_amount(
                event, rline, rline.price_subtotal, version, rate, basis='included')
    cells.append({
        'row_kind': 'total', 'response_id': response.id, 'raw_value': total, 'currency': currency.id,
        'missing': incomplete, 'has_normalized': normalized, 'normalized_value': total_normalized,
        'raw_display': f"{total:.{currency.decimal_places}f} {currency.name}"})
    for key in TERM_ROWS:
        cells.append(dict(
            _term_cell(version, key, version.amount_total, cost_normalized if normalized else None, incomplete),
            response_id=response.id))
    for criterion in event.criterion_ids:
        value = _criterion_value(criterion, version, total, version.line_ids)
        money = criterion.kind in ('price', 'total_cost')
        normalized_value = {'price': total_normalized, 'total_cost': cost_normalized}.get(criterion.kind, 0.0)
        cells.append({
            'row_kind': 'criterion', 'criterion_id': criterion.id, 'response_id': response.id,
            'raw_value': value, 'raw_display': f"{value:.{currency.decimal_places}f}" if money else f"{value:g}",
            'currency': currency.id if money else False,
            'has_normalized': normalized and money,
            'normalized_value': normalized_value if normalized else 0.0,
            'missing': incomplete and criterion.kind in AGGREGATE_KINDS or (
                criterion.kind in BLANK_ZERO_KINDS and not (
                    _term_given(version) if criterion.kind == 'terms' else value))})
    return cells


def _mark_best(event, cells):
    """Mark the best cell of each row: the lowest price, total and lead time, or the row's direction.

    Requirements: FR-SPM-0035
    A row is ranked on normalized values only when every cell of it holds one; cells of an offer
    that left a line out never win, and raw money in several currencies is not ranked.
    """
    criteria = {criterion.id: criterion for criterion in event.criterion_ids}
    rows = {}
    for cell in cells:
        if cell.get('missing') or not cell.get('rankable', True) or cell['row_kind'] == 'criterion' and (
                criteria[cell['criterion_id']].kind in GIVEN_KINDS):
            continue
        key = (cell['row_kind'], cell.get('event_line_id'), cell.get('criterion_id'), cell.get('term_key'))
        rows.setdefault(key, []).append(cell)
    for (kind, _line, criterion_id, term_key), row in rows.items():
        if kind == 'term':
            higher = TERM_ROWS[term_key]
            if higher is None:
                continue
        else:
            higher = kind == 'criterion' and criteria[criterion_id].direction == 'higher'
        money = bool(row[0].get('currency'))
        use_normalized = money and all(cell.get('has_normalized') for cell in row)
        if money and not use_normalized and len({cell['currency'] for cell in row}) > 1:
            continue
        values = [cell['normalized_value'] if use_normalized else cell['raw_value'] for cell in row]
        best = max(values) if higher else min(values)
        for cell, value in zip(row, values):
            cell['is_best'] = value == best


def compare(event):
    """Rebuild the stored comparison of the current quotations of the event, scored cell by cell, and return it.

    Requirements: FR-SPM-0035, FR-SPM-0037, FR-SPM-0038, BR-SPM-0007
    The criterion cells carry their score again after the rebuild, so the figures shown agree with
    the weighted score of the responses.
    """
    comparison = _build_comparison(event)
    _score_cells(event, comparison)
    return comparison


def _build_comparison(event):
    """Rebuild the cells of the stored comparison and return it, before any criterion is scored.

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
    _mark_best(event, cells)
    comparison = env['procurement.comparison'].search([('event_id', '=', event.id)])
    kept = {(cell.criterion_id.id, cell.response_id.id): (
                cell.manual_score, cell.manual_comment, cell.yes_no_value)
            for cell in comparison.line_ids if cell.row_kind == 'criterion'
            and (cell.manual_score or cell.manual_comment or cell.yes_no_value)}
    values = {
        'assumptions': comparison_assumptions(env, event), 'is_normalized': normalized,
        'computed_at': fields.Datetime.now(), 'line_ids': [(5, 0, 0)]}
    if comparison:
        comparison.write(values)
    else:
        comparison = env['procurement.comparison'].create(dict(values, event_id=event.id))
    rows = []
    for cell in cells:
        for internal in ('currency', 'uom', 'has_normalized', 'rankable'):
            cell.pop(internal, None)
        manual = kept.get((cell.get('criterion_id'), cell['response_id']))
        if manual:
            cell['manual_score'], cell['manual_comment'], cell['yes_no_value'] = manual
        rows.append(dict(cell, comparison_id=comparison.id))
    env['procurement.comparison.line'].create(rows)
    return comparison


SCORE_MAX = 100.0
# Order states in which the order linked to an event is still a draft the award may update.
DRAFT_ORDER_STATES = ('draft', 'sent')


def check_event_not_awarded(env, events):
    """Refuse a change or a deletion by a user of the evidence of an awarded event.

    Requirements: FR-SPM-0039, FR-SPM-0041
    The services of this module write with elevated rights and are not stopped.
    """
    if not env.su and any(event.state == 'awarded' for event in events):
        raise UserError(env._(
            "The event is awarded: its lines, criteria, comparison and responses are the evidence "
            "of the award and cannot be changed or deleted."))


def _lock_event(event):
    """Take the row lock of the event and reload it, so two awards or generations at once queue up."""
    env = event.env
    env.flush_all()
    env.cr.execute('SELECT id FROM procurement_sourcing_event WHERE id = %s FOR UPDATE', [event.id])
    env.invalidate_all()


def _score_row(values, higher):
    """The 0-100 scores of one criterion row: ``values`` maps a cell to its value, None when missing.

    Requirements: BR-SPM-0008
    100 x best / value where lower is better, 100 x value / best where higher is better; a missing
    value scores 0.
    """
    present = [value for value in values.values() if value is not None]
    best = (max(present) if higher else min(present)) if present else 0.0
    scores = {}
    for cell, value in values.items():
        if value is None:
            scores[cell] = 0.0
        elif higher:
            scores[cell] = SCORE_MAX * value / best if best > 0 else 0.0
        else:
            scores[cell] = SCORE_MAX * best / value if value > 0 else SCORE_MAX
    return scores


def _criterion_scores(event, criterion, cells):
    """The scores of the cells of one criterion, or None when the criterion cannot be scored.

    Requirements: BR-SPM-0008
    A price criterion is scored on normalized values only; without normalization it is left out.
    A manual criterion is scored with the 0-100 score the buyer gave, as it is: ``higher`` is None
    for it, and a cell with neither a score nor a comment is flagged missing. A yes/no criterion
    scores 100 for yes and 0 for no; a cell without an answer scores 0 and is flagged missing.
    """
    if criterion.kind == 'manual':
        return {cell: cell.manual_score or 0.0 for cell in cells}, None
    if criterion.kind == 'yes_no':
        return {cell: SCORE_MAX if cell.yes_no_value == 'yes' else 0.0 for cell in cells}, None
    money = criterion.kind in ('price', 'total_cost')
    if money and not event.is_normalized:
        return None, False
    values = {
        cell: None if cell.missing else (cell.normalized_value if money else cell.raw_value)
        for cell in cells}
    return values, criterion.direction == 'higher'


def _is_given(criterion, cell):
    """Whether the buyer gave the answer of a manual or yes/no cell."""
    if criterion.kind == 'yes_no':
        return bool(cell.yes_no_value)
    return bool(cell.manual_score or (cell.manual_comment or '').strip())


def _score_cells(event, comparison):
    """Score the criterion cells of the comparison and return the weighted totals and the weight sum.

    Requirements: FR-SPM-0038, BR-SPM-0008
    A cell scores its offer on one criterion, 0 to 100; a missing value scores 0 and is flagged on its cell.
    """
    cells = comparison.line_ids.filtered(lambda cell: cell.row_kind == 'criterion')
    totals = {response: 0.0 for response in event.response_ids.filtered('current_version_id')}
    weight_sum = 0.0
    for criterion in event.criterion_ids:
        row = cells.filtered(lambda cell: cell.criterion_id == criterion)
        values, higher = _criterion_scores(event, criterion, row)
        if values is None:
            continue
        manual = higher is None
        scores = values if manual else _score_row(values, higher)
        weight_sum += criterion.weight
        for cell, score in scores.items():
            unscored = not _is_given(criterion, cell) if manual else values[cell] is None
            cell.write({'score': score, 'missing': cell.missing or unscored})
            totals[cell.response_id] += criterion.weight * score
    return totals, weight_sum


def evaluate(event):
    """Rebuild the comparison, score every response by the weighted criteria and rank them.

    Requirements: FR-SPM-0038, BR-SPM-0008
    A response score is the weighted average of its criterion scores. Scores and ranks are
    written with elevated rights after the checks, since only this evaluation sets them.
    """
    env = event.env
    event.check_access('write')
    if event.state != 'open':
        raise UserError(env._("Only an open event can be evaluated."))
    totals, weight_sum = _score_cells(event, _build_comparison(event))
    result = {response: total / weight_sum if weight_sum else 0.0 for response, total in totals.items()}
    ordered = sorted(result.values(), reverse=True)
    for response in event.response_ids:
        # sudo: the score, rank and missing count are internal results only this evaluation writes
        response.sudo().write({
            'score': result.get(response, 0.0),
            'rank': ordered.index(result[response]) + 1 if response in result else 0,
            'missing_line_count': len(event.line_ids - response.current_version_id.line_ids.event_line_id)})
    return True


def _check_choices(env, event, choices):
    """Refuse a choice of a line or a response that is not the event's, unreadable, or not quoted."""
    if not choices:
        raise UserError(env._("Choose the response that wins at least one line."))
    for line, response in choices.items():
        if line.event_id != event or response.event_id != event:
            raise UserError(env._("A chosen line or response does not belong to this event."))
        response.check_access('read')
        version = response.current_version_id
        if not version.line_ids.filtered(lambda offer: offer.event_line_id == line):
            raise UserError(env._(
                "%(supplier)s did not quote %(line)s.", supplier=response.partner_id.name, line=line.name))


def _tell_unsuccessful(env, event, partners):
    """Tell each unsuccessful supplier that the event went to another offer.

    Requirements: FR-SPM-0041
    The mail goes to the supplier's own address; a supplier without one is told through its portal users.
    The text names neither the winner nor a price.
    """
    template = env.ref(AWARDED_OTHER_TEMPLATE)
    without_address = env['res.partner']
    for partner in partners:
        if partner.email:
            template.send_mail(event.id, force_send=False, email_values={
                'recipient_ids': [(6, 0, partner.ids)], 'email_to': False})
        else:
            without_address |= partner
    notify_suppliers(env, event, AWARDED_OTHER_TEMPLATE, without_address)


def award(event, choices, rationale, baseline_kind='first_average', budget_amount=0.0):
    """Award the lines of the event to the chosen responses and close the event.

    Requirements: FR-SPM-0039, FR-SPM-0041, FR-SPM-0103
    ``choices`` maps an event line to the response that wins it. A rationale is required. The
    savings against the chosen baseline are stored with the award. The
    chosen offers' lines are marked awarded and the other responses not awarded; the unsuccessful
    suppliers are told when the event says so, without the winner or any price.
    """
    env = event.env
    event.check_access('write')
    rationale = (rationale or '').strip()
    if not rationale:
        raise UserError(env._("An award rationale is required: say why this offer won."))
    _lock_event(event)
    if event.state != 'open':
        raise UserError(env._("Only an open event can be awarded."))
    _check_choices(env, event, choices)
    for response in env['procurement.sourcing.response'].union(*choices.values()):
        supplier_control.check_supplier_allowed(env, event, response.partner_id, event.company_id, 'award')
    evaluate(event)
    savings = report.award_savings(event, choices, baseline_kind, budget_amount)
    offers = env['procurement.sourcing.response.line']
    for line, response in choices.items():
        offers |= response.current_version_id.line_ids.filtered(lambda offer: offer.event_line_id == line)
    # sudo: the award mark of an offered line is written by the award only; the checks are above
    offers.sudo().write({'awarded': True})
    winners = env['procurement.sourcing.response']
    for response in event.response_ids:
        if response in choices.values():
            winners |= response
            whole = all(offer.awarded for offer in response.current_version_id.line_ids)
            state = 'awarded' if whole else 'partly_awarded'
        else:
            state = 'not_awarded'
        # sudo: the state of a response is a workflow field no user writes directly
        response.sudo().write({'state': state})
    # sudo: the state and the award evidence are workflow fields no user writes directly
    event.sudo().write({
        'state': 'awarded', 'award_rationale': rationale, 'awarded_date': fields.Datetime.now(), **savings})
    if event.inform_unsuccessful:
        _tell_unsuccessful(env, event, (event.response_ids - winners).filtered('current_version_id').partner_id)
    audit.log(
        env, 'award', event, {'state': 'open'},
        {'state': 'awarded', 'winners': winners.partner_id.mapped('name'),
         'lines': {line.name: response.partner_id.name for line, response in choices.items()}},
        rationale)
    return True


def _order_values(event, response, version, request_ids):
    """The values of the draft order of one awarded supplier."""
    values = {
        'partner_id': response.partner_id.id, 'company_id': event.company_id.id,
        'currency_id': version.currency_id.id, 'payment_term_id': version.payment_term_id.id,
        'spm_sourcing_event_id': event.id, 'spm_request_ids': [(6, 0, request_ids)],
        'origin': event.name}
    if event.picking_type_id:
        values['picking_type_id'] = event.picking_type_id.id
    return values


def _order_line_values(offer, order_date):
    """The values of the order line of one awarded offer; the expected date follows its lead time."""
    event_line = offer.event_line_id
    return {
        'spm_sourcing_event_line_id': event_line.id, 'product_id': event_line.product_id.id,
        'name': event_line.name, 'product_qty': offer.quantity, 'uom_id': offer.uom_id.id,
        'price_unit': offer.price_unit, 'discount': offer.discount, 'tax_ids': [(6, 0, offer.tax_ids.ids)],
        'date_planned': order_date + timedelta(days=offer.lead_time_days)}


def _sync_order(env, event, response, offers):
    """Create the draft order of a supplier or update the one linked to the event; None when confirmed.

    Requirements: FR-SPM-0040
    """
    orders = env['purchase.order'].search([
        ('spm_sourcing_event_id', '=', event.id), ('partner_id', '=', response.partner_id.id),
        ('state', '!=', 'cancel')])
    drafts = orders.filtered(lambda order: order.state in DRAFT_ORDER_STATES)
    if orders and not drafts:
        return orders[0], False
    version = response.current_version_id
    values = _order_values(event, response, version, event.request_ids.ids)
    order = drafts[:1]
    if order:
        order.write(values)
        order.order_line.filtered(
            lambda line: line.spm_sourcing_event_line_id
            and line.spm_sourcing_event_line_id not in offers.event_line_id).unlink()
    else:
        order = env['purchase.order'].create(dict(values, date_order=fields.Datetime.now()))
    existing = {line.spm_sourcing_event_line_id: line for line in order.order_line}
    lines = []
    for offer in offers:
        line_values = _order_line_values(offer, order.date_order)
        current = existing.get(offer.event_line_id)
        lines.append((1, current.id, line_values) if current else (0, 0, line_values))
    order.write({'order_line': lines})
    return order, True


def generate_orders(event):
    """Create or update one draft purchase order per awarded supplier; a confirmed order is left alone.

    Requirements: FR-SPM-0040
    Rights are the user's own. An order is never confirmed here. The result opens the orders, or
    names the orders found already confirmed.
    """
    env = event.env
    event.check_access('write')
    _lock_event(event)
    if event.state != 'awarded':
        raise UserError(env._("Award the event before generating its purchase orders."))
    orders = env['purchase.order']
    confirmed = []
    for response in event.response_ids.filtered(lambda r: r.state in ('awarded', 'partly_awarded')):
        offers = response.current_version_id.line_ids.filtered('awarded')
        order, changed = _sync_order(env, event, response, offers)
        orders |= order
        if not changed:
            confirmed.append(order.name)
    action = {
        'type': 'ir.actions.act_window', 'name': env._("Purchase orders"), 'res_model': 'purchase.order',
        'view_mode': 'list,form', 'domain': [('id', 'in', orders.ids)]}
    if confirmed:
        return {
            'type': 'ir.actions.client', 'tag': 'display_notification',
            'params': {
                'type': 'warning', 'sticky': True, 'next': action,
                'message': env._(
                    "The existing order %s is confirmed and was not changed.", ', '.join(confirmed))}}
    return action
