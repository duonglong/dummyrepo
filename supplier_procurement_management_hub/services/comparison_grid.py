"""The side-by-side grid of a quotation comparison: one column per supplier.

Requirements: FR-SPM-0035, FR-SPM-0038
"""
from markupsafe import Markup

from odoo.exceptions import AccessError

BEST = 'fw-bold text-success'
MISSING = 'text-danger'


def _cell(css, text):
    return Markup('<td class="{}">{}</td>').format(css, text)


def _row(label, cells, css=''):
    return Markup('<tr class="{}"><th scope="row">{}</th>{}</tr>').format(css, label, Markup('').join(cells))


def _section(label, width):
    return Markup('<tr class="table-light"><th colspan="{}">{}</th></tr>').format(width, label)


def _ranked(comparison):
    """Return the compared responses, best rank first, those without a rank last."""
    responses = comparison.line_ids.response_id
    try:
        return responses.sorted(lambda response: (response.rank or len(responses) + 1, response.partner_id.name or ''))
    except AccessError:
        return responses.sorted(lambda response: response.partner_id.name or '')


def _value_cell(env, cell):
    """Return the table cell of one comparison cell: its value, the word Missing, or a score for a criterion."""
    if cell.missing and cell.row_kind != 'criterion':
        return _cell(MISSING, env._("Missing"))
    if cell.row_kind == 'criterion':
        if cell.missing:
            return _cell(MISSING, env._("Missing"))
        text = f"{cell.score:.2f}"
        if cell.raw_display:
            text = f"{text} ({cell.raw_display})"
        return _cell(BEST if cell.is_best else '', text)
    return _cell(BEST if cell.is_best else '', cell.raw_display or '-')


def _score_rows(env, responses):
    try:
        scores = [f"{response.score:.2f}" for response in responses]
        ranks = [f"#{response.rank}" if response.rank else '-' for response in responses]
    except AccessError:
        return []
    return [
        _row(env._("Weighted score"), [_cell('fw-bold', score) for score in scores]),
        _row(env._("Rank"), [_cell('', rank) for rank in ranks]),
    ]


def render(comparison) -> Markup:
    """Return the grid of a comparison as HTML: suppliers as columns by rank, rows of lines, terms and criteria.

    Requirements: FR-SPM-0035, FR-SPM-0038
    The best value of a row is bold green and a value a supplier did not quote reads Missing.
    """
    env = comparison.env
    responses = _ranked(comparison)
    if not responses:
        return Markup('')
    width = len(responses) + 1
    cells = {(cell.row_kind, cell.event_line_id.id, cell.criterion_id.id, cell.term_key, cell.response_id.id): cell
             for cell in comparison.line_ids}

    def row(label, kind, line=False, criterion=False, term=False):
        return _row(label, [
            _value_cell(env, cells[key]) if (key := (kind, line, criterion, term, response.id)) in cells else _cell('', '-')
            for response in responses])

    event = comparison.event_id
    terms = dict(comparison.line_ids._fields['term_key']._description_selection(env))
    rows = _score_rows(env, responses)
    rows.append(_section(env._("Lines"), width))
    rows.extend(row(line.name, 'line', line=line.id) for line in event.line_ids)
    rows.append(row(env._("Total"), 'total'))
    rows.append(_section(env._("Terms"), width))
    rows.extend(row(label, 'term', term=key) for key, label in terms.items())
    if event.criterion_ids:
        rows.append(_section(env._("Criteria"), width))
        rows.extend(row(f"{criterion.name} ({criterion.weight:g})", 'criterion', criterion=criterion.id)
                    for criterion in event.criterion_ids)
    header = Markup('').join(Markup('<th scope="col">{}</th>').format(response.partner_id.name) for response in responses)
    return Markup(
        '<table class="table table-sm table-bordered o_spm_comparison_grid"><thead><tr><th/>{}</tr></thead>'
        '<tbody>{}</tbody></table>').format(header, Markup('').join(rows))
