"""Check an import file without importing it.

The file is read and its columns are matched to fields by the native import (``base_import.import``);
the rows are then checked here, in memory: nothing calls ``load``, so no record, sequence or mail is
created, not even temporarily (ADR-DGQ-011).
"""
import csv
import datetime
import hashlib
import io
from collections import namedtuple

from odoo.exceptions import AccessError, UserError
from odoo.models import MAGIC_COLUMNS

from ..const import IMPORT_MAX_BYTES, IMPORT_MAX_ROWS, IMPORT_RULE_TYPES, IMPORT_VALUE_MAX_LENGTH
from . import scan as scan_service
from . import validation

FILE_MIMETYPES = {
    'csv': 'text/csv',
    'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
}
HEADER_ROW = 1
PREVIEW_ROWS = 10

# A mapped column: its place among the mapped cells of a row, its header, its field and, for a link
# given by external or database id, the kind of that id.
Column = namedtuple('Column', 'position header field id_kind')
# rows: [(row number, mapped cells)]; repeats: {row number: (first row number, first column)}.
LoadedFile = namedtuple('LoadedFile', 'columns rows repeats')

# The file of the check in progress: reading a large file for each batch would take longer than the batches.
_LOADED = {}


def file_checksum(session):
    """The sha256 of the uploaded file."""
    return hashlib.sha256(session.file.content).hexdigest()


def load_file(session):
    """Read, validate and map the file of a session; the last one read is kept for the next batch.

    Raises ``UserError`` for a file that is missing, too large, empty, unreadable or too long.

    Requirements: FR-DGQ-0058
    """
    key = (session.env.cr.dbname, session.id, session.file_checksum, tuple(session.duplicate_field_ids.ids))
    if _LOADED.get('key') != key:
        _LOADED.clear()
        _LOADED.update(key=key, loaded=_read_file(session))
    return _LOADED['loaded']


def forget_file():
    """Release the file kept in memory."""
    _LOADED.clear()


def _read_file(session):
    env = session.env
    value = session.file
    if not value:
        raise UserError(env._("Choose a CSV or XLSX file to check."))
    if session.file_type not in FILE_MIMETYPES:
        raise UserError(env._("Only CSV and XLSX files can be checked."))
    if value.size > IMPORT_MAX_BYTES:
        raise UserError(env._("A file may be at most %(size)s MB.", size=IMPORT_MAX_BYTES // (1024 * 1024)))
    # sudo: the native import keeps its working record in a transient model
    reader = env['base_import.import'].sudo().create({
        'res_model': session.res_model,
        'file': value,
        'file_name': session.file_name,
        'file_type': FILE_MIMETYPES[session.file_type],
    })
    options = {'has_headers': True, 'quoting': '"', 'separator': '', 'encoding': ''}
    try:
        result = reader._read_file(options)
    except UserError:
        raise
    except Exception as error:  # noqa: BLE001 (any reader failure means the file cannot be read)
        raise UserError(env._("The file cannot be read: %s", error)) from error
    if not result or not result[1]:
        raise UserError(env._("The file is empty."))
    numbered = _numbered_rows(session, options, result[1])
    if len(numbered) < 2:
        raise UserError(env._("The file has a header but no row to check."))
    if len(numbered) - 1 > IMPORT_MAX_ROWS:
        raise UserError(env._("A file may hold at most %(rows)s rows.", rows=f"{IMPORT_MAX_ROWS:,}"))
    return _map_columns(session, reader, options, numbered)


def _numbered_rows(session, options, rows):
    """The rows of the file with their number: the header is row 1.

    A CSV file is numbered by its line, and a line of empty cells is a row, which the native reader
    would drop. An XLSX file is numbered by the rows the native reader keeps.
    """
    if session.file_type != 'csv':
        return list(enumerate(rows, HEADER_ROW))
    text = session.file.content.decode(options['encoding'])
    reader = csv.reader(io.StringIO(text), quotechar=options['quoting'], delimiter=options['separator'] or ',')
    width = len(rows[0])
    numbered = []
    for cells in reader:
        if cells and (width == 1 or any(cell.strip() for cell in cells)):
            numbered.append((reader.line_num, cells))
    return numbered


def _map_columns(session, reader, options, numbered):
    env = session.env
    model = env[session.res_model]
    width = len(numbered[0][1])
    rows = [(number, (cells + [''] * width)[:width]) for number, cells in numbered]
    headers = rows[0][1]
    preview = [cells for _number, cells in rows[1:PREVIEW_ROWS + 1]]
    header_types = reader._extract_headers_types(headers, preview, options)
    suggestions = reader._get_mapping_suggestions(
        headers, header_types, reader.get_fields_tree(session.res_model))
    columns, paths = [], []
    for (index, header), suggestion in sorted(suggestions.items()):
        path = suggestion and suggestion['field_path']
        field = path and model._fields.get(path[0])
        if field is None or len(path) > 2 or (len(path) == 2 and path[1] not in ('id', '.id')):
            continue
        columns.append(Column(len(columns), header, field, path[1] if len(path) == 2 else ''))
        paths.append((index, '/'.join(path)))
    if not columns:
        raise UserError(env._("No column of the file matches a field of %s.", session.model_id.name))
    data = [[_cell(cells[index], column.field) for (index, _path), column in zip(paths, columns)]
            for _number, cells in rows[1:]]
    try:
        reader._parse_import_data(data, [path for _index, path in paths], options)
    except Exception as error:  # noqa: BLE001 (the native parser raises its own error type for a bad date or number)
        raise UserError(env._("The file cannot be read: %s", error)) from error
    numbers = [number for number, _cells in rows[1:]]
    return LoadedFile(columns, list(zip(numbers, data)), _find_repeats(session, columns, numbers, data))


def _cell(value, field):
    """A cell as the native parser expects it: text, except a date read from an XLSX cell."""
    if isinstance(value, datetime.date) and field.type in ('date', 'datetime'):
        return value
    return '' if value is None else str(value)


def _find_repeats(session, columns, numbers, data):
    """The rows that repeat an earlier row on the duplicate fields: ``{row number: (first row number, column)}``.

    Requirements: FR-DGQ-0059
    """
    names = set(session.duplicate_field_ids.mapped('name'))
    keyed = [column for column in columns if column.field.name in names and not column.id_kind]
    if not keyed:
        return {}
    seen, repeats = {}, {}
    for number, cells in zip(numbers, data):
        key = tuple(str(cells[column.position]).strip().casefold() for column in keyed)
        if not any(key):
            continue
        if key in seen:
            repeats[number] = (seen[key], keyed[0])
        else:
            seen[key] = number
    return repeats


def required_fields(model):
    """The stored fields of a model that must be filled and have no default value."""
    names = [
        name for name, field in model._fields.items()
        if field.required and field.store and not field.compute and name not in MAGIC_COLUMNS
        and field.type not in ('one2many', 'many2many')]
    defaults = model.default_get(names)
    return [model._fields[name] for name in names if name not in defaults]


def match_link(env, field, value, id_kind):
    """The number of records a link value matches and the first one, as the native import matches it.

    A value is matched by name, by external id or by database id; ``(0, False)`` for none.
    """
    comodel = env[field.comodel_name]
    if id_kind == 'id':
        found = env.ref(value, raise_if_not_found=False)
        return (1, found.id) if found is not None and found._name == comodel._name else (0, False)
    if id_kind == '.id':
        found = comodel.browse(int(value)).exists() if value.isdigit() else comodel
        return (1, found.id) if found else (0, False)
    matches = comodel.name_search(value, [], '=', 2)
    return len(matches), matches[0][0] if matches else False


def check_batch(session, loaded, first, size):
    """Check ``size`` rows of the file from row index ``first``: the findings, as values of ``data.quality.import.row``.

    Requirements: FR-DGQ-0058, FR-DGQ-0059
    """
    return BatchCheck(session, loaded).run(first, size)


class BatchCheck:
    """The checks of the rows of one batch: required fields, links, repeats and quality rules."""

    def __init__(self, session, loaded):
        user = session.user_id or session.env.user
        self.env = session.env(context=dict(session.env.context, lang=user.lang or 'en_US'))
        self.session = session
        self.loaded = loaded
        self.model = session.env[session.res_model]
        # the links are matched as the person who checks may see the records
        self.link_env = session.env(
            user=user.id, context=dict(session.env.context, allowed_company_ids=[session.company_id.id]))
        self.usable = self.env['data.quality.field.config']._get_usable_field_names(session.res_model)
        self.by_field = {}
        for column in loaded.columns:
            self.by_field.setdefault(column.field.name, column)
        self.required = required_fields(self.model)
        self.lookups = {}
        self.findings = []

    def run(self, first, size):
        records, origin = [], {}
        if not first:
            self._check_missing_columns()
        for number, cells in self.loaded.rows[first:first + size]:
            values = self._read_row(number, cells)
            if number in self.loaded.repeats:
                earlier, column = self.loaded.repeats[number]
                self._add(number, column, 'error', self.env._("Repeats row %(row)s.", row=earlier), cells)
            # sudo: the row is judged as the scans judge records, whatever the user may read
            record = self.model.sudo().new(values)
            records.append(record)
            origin[record.id] = (number, cells)
        # sudo: see above
        self._check_rules(self.model.sudo().concat(*records), origin)
        return self.findings

    def _add(self, number, column, severity, message, cells=None, field_name=''):
        value = False
        field = column.field if column else None
        if field and cells is not None and field.name in self.usable:
            value = str(cells[column.position])[:IMPORT_VALUE_MAX_LENGTH]
        self.findings.append({
            'session_id': self.session.id,
            'row_number': number,
            'column': column.header if column else False,
            'field_name': field.name if field else field_name,
            'severity': severity,
            'message': message,
            'value': value,
        })

    def _check_missing_columns(self):
        for field in self.required:
            if field.name not in self.by_field:
                self._add(HEADER_ROW, None, 'error', self.env._(
                    "The file has no column for the required field %(field)s.",
                    field=field._description_string(self.env)), field_name=field.name)

    def _read_row(self, number, cells):
        """The values of one row for its in-memory record; the empty required fields and bad links are findings."""
        values = {}
        model = self.model
        if 'company_id' in model._fields:
            values['company_id'] = self.session.company_id.id
        required = {field.name for field in self.required}
        for column in self.loaded.columns:
            field = column.field
            cell = cells[column.position]
            text = cell.strip() if isinstance(cell, str) else cell
            if not text:
                if field.name in required:
                    self._add(number, column, 'error', self.env._(
                        "%(field)s is required.", field=field._description_string(self.env)), cells)
                continue
            self._read_cell(number, column, cells, text, values)
        return values

    def _read_cell(self, number, column, cells, text, values):
        field = column.field
        label = field._description_string(self.env)
        try:
            if field.type == 'many2one':
                count, found = self._link(field, text, column.id_kind)
                if count == 1:
                    values[field.name] = found
                elif count:
                    self._add(number, column, 'error', self.env._(
                        "'%(value)s' matches several records of %(model)s.",
                        value=text, model=self.env[field.comodel_name]._description), cells)
                else:
                    self._add(number, column, 'error', self.env._(
                        "No record of %(model)s matches '%(value)s'.",
                        value=text, model=self.env[field.comodel_name]._description), cells)
            elif field.type in ('char', 'text'):
                values[field.name] = text
            elif field.type == 'integer':
                values[field.name] = int(text)
            elif field.type in ('float', 'monetary'):
                values[field.name] = float(text)
            elif field.type in ('date', 'datetime'):
                values[field.name] = text
        except AccessError:
            self._add(number, column, 'error', self.env._(
                "You are not allowed to link to records of %s.", self.env[field.comodel_name]._description), cells)
        except ValueError:
            self._add(number, column, 'error', self.env._("%(field)s has the invalid value '%(value)s'.",
                                                          field=label, value=text), cells)

    def _link(self, field, text, id_kind):
        key = (field.name, text, id_kind)
        if key not in self.lookups:
            self.lookups[key] = match_link(self.link_env, field, text, id_kind)
        return self.lookups[key]

    def _rules(self):
        """The rules of the session an import row can be checked against."""
        session = self.session
        # sudo: the check runs as the app; a rule of the model applies whoever uploaded the file
        rules = session.sudo().rule_ids or self.env['data.quality.rule'].sudo().search(
            [('model_id', '=', session.model_id.id)])
        return rules.filtered(lambda rule: (
            rule.rule_type in IMPORT_RULE_TYPES
            and (not rule.company_ids or session.company_id in rule.company_ids)
            and scan_service.rule_fields(rule) <= set(self.by_field)
            and not scan_service.not_run_reason(self.env, rule)))

    def _check_rules(self, records, origin):
        """A failure of a critical rule is an error, of any other rule a warning.

        Requirements: FR-DGQ-0059
        """
        for rule in self._rules():
            applicable = scan_service.applicable_records(rule, records)
            if not applicable:
                continue
            severity = 'error' if rule.severity == 'critical' else 'warning'
            for (record_id, _rule_id), outcome in validation.check(rule, applicable).items():
                if outcome.status != validation.FAIL:
                    continue
                params = outcome.params or {}
                names = params.get('fields') or [params.get('field')] or []
                reason = validation.explain(self.env, outcome.code, params)
                message = self.env._("%(rule)s: %(reason)s", rule=rule.name, reason=reason)
                number, cells = origin[record_id]
                for name in [name for name in names if name] or [False]:
                    self._add(number, self.by_field.get(name), severity, message, cells, field_name=name or '')
