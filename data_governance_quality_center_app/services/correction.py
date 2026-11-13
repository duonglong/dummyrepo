"""The engine of the corrections: the built-in actions and the processing of one batch of targets.

An action is a function ``(value, record, params) -> new value`` that raises ``DgqSkip`` with the
reason when the value cannot be corrected. Actions are registered through the registry so that an
installed module can add its own (NFR-DGQ-0010). Nothing here keeps state.
"""
import logging

from odoo import fields
from odoo.exceptions import UserError
from odoo.fields import Domain
from odoo.tools.mail import email_normalize
from odoo.tools.safe_eval import safe_eval

from odoo.addons.phone_validation.tools import phone_validation

from ..const import JOB_BATCH_SIZE

_logger = logging.getLogger(__name__)

TEXT_TYPES = ('char', 'text', 'html')
DEFAULT_VALUE_TYPES = ('char', 'text', 'integer', 'float', 'selection')
LINK_TYPES = ('many2one',)
TEXT_CASES = {
    'upper': str.upper,
    'lower': str.lower,
    'title': str.title,
}


class DgqSkip(Exception):
    """A value an action cannot correct; the message is the reason shown in the log."""


def convert_default(ttype, text):
    """The default value text as a value of the field type.

    Requirements: FR-DGQ-0046
    """
    if ttype == 'integer':
        return int(text)
    if ttype == 'float':
        return float(text)
    return text


def action_default(value, record, params):
    """Fill an empty value with the default value of the correction."""
    if value not in (False, None, ''):
        return value
    try:
        return convert_default(params['ttype'], params['default_value'])
    except (TypeError, ValueError) as error:
        raise DgqSkip(record.env._("The default value does not fit the field.")) from error


def action_trim(value, record, params):
    """Remove the spaces around a text."""
    return value.strip() if value else value


def action_email(value, record, params):
    """Trim an e-mail and put it in lower case; a text that is no address is skipped."""
    if not value:
        return value
    normalized = email_normalize(value)
    if not normalized:
        raise DgqSkip(record.env._("The e-mail cannot be normalized."))
    return normalized


def action_phone(value, record, params):
    """Write a phone number in the international format of its country."""
    if not value:
        return value
    country = (record.country_id if 'country_id' in record._fields else False) or record.env.company.country_id
    try:
        return phone_validation.phone_format(
            value, country.code, country.phone_code, force_format='INTERNATIONAL')
    except UserError as error:
        raise DgqSkip(record.env._("The phone number cannot be normalized.")) from error


def action_text_case(value, record, params):
    """Change the case of a text to upper, lower or title case."""
    return TEXT_CASES[params['text_case']](value) if value else value


def action_country_state(value, record, params):
    """Replace a local spelling of a country or state by the one of the mapping."""
    if not value:
        return value
    spelling = (value.display_name if hasattr(value, 'display_name') else value).strip().lower()
    mapping = params['mappings'].get(spelling)
    if not mapping:
        raise DgqSkip(record.env._("No mapping for this country or state."))
    country, state = mapping
    if params['relation'] == 'res.country.state':
        target = state or False
    elif params['relation'] == 'res.country':
        target = country
    else:
        target = (state or country).name
    if not target:
        raise DgqSkip(record.env._("No mapping for this country or state."))
    return target.id if hasattr(target, 'id') else target


def action_link(value, record, params):
    """Replace a link to one record by a link to another."""
    return params['link_to'] if value == params['link_from'] else value


def builtin_actions(env):
    """The actions the app provides: descriptors of the registry.

    Requirements: FR-DGQ-0046
    """
    _ = env._
    return [
        {'key': 'default', 'label': _("Set a default value"), 'handler': action_default,
         'field_types': DEFAULT_VALUE_TYPES},
        {'key': 'trim', 'label': _("Trim spaces"), 'handler': action_trim,
         'field_types': TEXT_TYPES},
        {'key': 'normalize_email', 'label': _("Normalize e-mail"), 'handler': action_email,
         'field_types': TEXT_TYPES},
        {'key': 'normalize_phone', 'label': _("Normalize phone"), 'handler': action_phone,
         'field_types': TEXT_TYPES},
        {'key': 'text_case', 'label': _("Change text case"), 'handler': action_text_case,
         'field_types': TEXT_TYPES},
        {'key': 'country_state', 'label': _("Standardize country and state"),
         'handler': action_country_state, 'field_types': TEXT_TYPES + LINK_TYPES},
        {'key': 'replace_link', 'label': _("Replace a link"), 'handler': action_link,
         'field_types': LINK_TYPES},
    ]


def stored_value(record, field_name):
    """A field value as plain data: a link is its id."""
    value = record[field_name]
    return value.id if hasattr(value, 'id') else value


def target_ids(correction):
    """The ids of the records a correction looks at, in id order.

    A filter of the correction narrows the records of its model and companies; a correction with
    no filter and a rule looks at the records with an open issue of that rule.

    Requirements: FR-DGQ-0047
    """
    env = correction.env
    # sudo: the engine reads the scope of the correction; each record's access is checked per user
    model = env[correction.model_id.model].sudo().with_context(active_test=False)
    domain = Domain.TRUE
    if correction.domain:
        domain &= Domain(safe_eval(correction.domain))
    elif correction.rule_id:
        if 'data.quality.issue' not in env:
            return []
        # sudo: the issues of the rule, whoever asked
        issues = env['data.quality.issue'].sudo().search([
            ('rule_id', '=', correction.rule_id.id), ('state', 'in', ('open', 'assigned')),
            ('res_model', '=', model._name)])
        domain &= Domain('id', 'in', issues.mapped('res_id'))
    if correction.company_ids and 'company_id' in model._fields:
        domain &= Domain('company_id', 'in', [False, *correction.company_ids.ids])
    return model._search(domain, order='id').get_result_ids()


def action_params(correction):
    """The settings the action of a correction reads, as plain data."""
    field = correction.field_id
    mappings = {}
    for mapping in correction.mapping_ids:
        mappings[mapping.source_text.strip().lower()] = (mapping.country_id, mapping.state_id)
    return {
        'ttype': field.ttype,
        'relation': field.relation,
        'default_value': correction.default_value,
        'text_case': correction.text_case,
        'link_from': correction.link_from_id,
        'link_to': correction.link_to_id,
        'mappings': mappings,
    }


def run_batch(run):
    """Process the next batch of targets of a run; return ``(processed, remaining)``.

    A dry run logs what would change and writes nothing; a real run writes each changed value with
    the ORM, one savepoint per record, so a refused write is logged failed and the batch goes on.
    The checkpoint is the last id handled, so a resumed run continues after it.

    Requirements: FR-DGQ-0047, NFR-DGQ-0005
    """
    correction = run.correction_id
    if not run.res_ids:
        run.res_ids = target_ids(correction)
    pending = [rid for rid in run.res_ids if rid > run.last_id]
    batch, rest = pending[:JOB_BATCH_SIZE], pending[JOB_BATCH_SIZE:]
    if batch:
        _handle_batch(run, batch)
        run.last_id = batch[-1]
    return len(batch), len(rest)


def _handle_batch(run, ids):
    env = run.env
    correction = run.correction_id
    model_name = correction.model_id.model
    field_name = correction.field_id.name
    # sudo: the app writes the corrections; readability is checked for the user just below
    records = env[model_name].sudo().with_context(active_test=False).browse(ids).exists()
    readable = env[model_name].with_user(run.user_id).with_context(active_test=False).search(
        [('id', 'in', records.ids)]).ids
    protected = correction._dgq_is_protected() and not correction.protected_authorized
    handler = env['data.quality.registry']._dgq_find('correction_actions', correction.action)['handler']
    params = action_params(correction)
    real = run.kind == 'run'
    counts = dict.fromkeys(('changed', 'unchanged', 'skipped', 'failed'), 0)
    logs = []

    def line(record, state, reason=False, before=None, after=None):
        counts[{'would_change': 'changed'}.get(state, state)] += 1
        logs.append({
            'run_id': run.id, 'res_model': model_name, 'res_id': record.id,
            'company_id': record.company_id.id if 'company_id' in record._fields else False,
            'field_name': field_name, 'value_before': before, 'value_after': after,
            'state': state, 'reason': reason, 'user_id': run.user_id.id,
            'date': fields.Datetime.now(),
        })

    for record in records:
        if protected:
            line(record, 'skipped', env._("The record is protected."))
            continue
        if record.id not in readable:
            line(record, 'skipped', env._("The record cannot be read by the user."))
            continue
        before = stored_value(record, field_name)
        try:
            after = handler(record[field_name], record, params)
        except DgqSkip as skip:
            line(record, 'skipped', str(skip))
            continue
        after = after.id if hasattr(after, 'id') else after
        if after == before:
            counts['unchanged'] += 1
            continue
        if not real:
            line(record, 'would_change', before=before, after=after)
            continue
        try:
            with env.cr.savepoint():
                record.write({field_name: after})
        except Exception as error:  # noqa: BLE001 (any refusal of the target is logged, the run goes on)
            _logger.warning("dgq correction %s record %s %s: write refused (%s)",
                            correction.id, model_name, record.id, type(error).__name__)
            line(record, 'failed', str(error), before=before)
            continue
        line(record, 'changed', before=before, after=after)
    # sudo: nobody creates log lines by hand, the engine writes them
    env['data.quality.correction.log'].sudo().create(logs)
    run.write({
        'count_changed': run.count_changed + counts['changed'],
        'count_unchanged': run.count_unchanged + counts['unchanged'],
        'count_skipped': run.count_skipped + counts['skipped'],
        'count_failed': run.count_failed + counts['failed'],
    })
