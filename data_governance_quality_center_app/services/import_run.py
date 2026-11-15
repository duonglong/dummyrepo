"""Import a checked file through the native import and build its error report (ADR-DGQ-011).

A strict session imports only a file without errors; a warnings-only session leaves out the rows with
errors. Rows are imported in batches by ``base_import.import``, so every native rule of the import still
applies. The error report is the original file cut down to the rows that have findings.
"""
import base64
import csv
import datetime
import io
from collections import defaultdict

import xlsxwriter

from odoo.exceptions import UserError

from . import import_check

MESSAGE_SEPARATOR = '\n'
SEVERITY_ORDER = ('error', 'warning')
# The options of the native import for the file built from a batch of rows.
BATCH_OPTIONS = {
    'has_headers': True, 'quoting': '"', 'separator': ',', 'encoding': 'utf-8',
    'date_format': '', 'datetime_format': '', 'float_thousand_separator': ',', 'float_decimal_separator': '.',
}


def refusal(session):
    """The message that refuses the import of a session, or None when it may go ahead.

    Requirements: FR-DGQ-0060
    """
    if session.mode == 'strict' and session.error_row_count:
        return session.env._(
            "Import refused: %(count)s rows have errors and the session is in strict mode.",
            count=session.error_row_count)
    return None


def _error_numbers(session):
    """The row numbers of the file that hold an error."""
    # sudo: the findings are written and read by the app only
    groups = session.env['data.quality.import.row'].sudo()._read_group(
        [('session_id', '=', session.id), ('severity', '=', 'error')], ['row_number'])
    return {number for (number,) in groups}


def import_batch(session, loaded, first, size):
    """Import ``size`` rows from index ``first`` of the rows left to import.

    Returns ``(rows to import in all, rows created, errors)``; an error is ``(row number, message)``.

    Requirements: FR-DGQ-0060
    """
    skip = _error_numbers(session)
    rows = [(number, cells) for number, cells in loaded.rows if number not in skip]
    chunk = rows[first:first + size]
    if not chunk:
        return len(rows), 0, []
    paths = [
        column.field.name + (f'/{column.id_kind}' if column.id_kind else '') for column in loaded.columns]
    headers = [column.header for column in loaded.columns]
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(headers)
    writer.writerows([_text(cell) for cell in cells] for _number, cells in chunk)
    env = session.env
    user = session.user_id or env.user
    # sudo: the native import keeps its working record in a transient model; the import itself runs as the user
    reader = env['base_import.import'].sudo().create({
        'res_model': session.res_model,
        'file': base64.b64encode(output.getvalue().encode()).decode(),
        'file_name': session.file_name,
        'file_type': import_check.FILE_MIMETYPES['csv'],
    })
    user_env = env(user=user.id, context=dict(
        env.context, allowed_company_ids=[session.company_id.id], lang=user.lang or 'en_US'))
    result = reader.with_env(user_env).execute_import(paths, headers, dict(BATCH_OPTIONS))
    errors = [
        (chunk[min((message.get('rows') or {}).get('from') or 0, len(chunk) - 1)][0], str(message['message']))
        for message in result['messages'] if message.get('type') == 'error']
    return len(rows), len(result.get('ids') or []), errors


def _text(cell):
    return cell.isoformat() if isinstance(cell, (datetime.date, datetime.datetime)) else cell


def error_report(session):
    """The original file cut down to the rows with findings, with the severity and the messages added.

    Returns ``(content, file name, mimetype)``; the format is the one of the file. Findings of the
    header row (a missing column, a rule that was not checked) concern the file, not a row, and are
    left out.

    Requirements: FR-DGQ-0061
    """
    env = session.env
    if session.state != 'done' or session.job_kind != 'check':
        raise UserError(env._("The error report is available once the check has finished."))
    # sudo: the findings are written and read by the app only; the session itself was read as the user
    findings = session.sudo().row_ids.filtered(lambda finding: finding.row_number > import_check.HEADER_ROW)
    by_row = defaultdict(list)
    for finding in findings.sorted(key=lambda f: (f.row_number, SEVERITY_ORDER.index(f.severity), f.id)):
        by_row[finding.row_number].append(finding)
    _reader, _options, numbered = import_check.read_numbered_rows(session)
    labels = dict(findings._fields['severity']._description_selection(env))
    header = list(numbered[0][1]) + [env._("Severity"), env._("Messages")]
    rows = [
        list(cells) + [
            labels[found[0].severity], MESSAGE_SEPARATOR.join(finding.message for finding in found)]
        for number, cells in numbered[1:] if (found := by_row.get(number))]
    base = (session.file_name or 'import').rsplit('.', 1)[0]
    name = f"{base}_errors.{session.file_type}"
    if session.file_type == 'xlsx':
        return _xlsx(header, rows), name, import_check.FILE_MIMETYPES['xlsx']
    return _csv(header, rows), name, import_check.FILE_MIMETYPES['csv']


def _csv(header, rows):
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(header)
    writer.writerows([_text(cell) for cell in row] for row in rows)
    return output.getvalue().encode()


def _xlsx(header, rows):
    output = io.BytesIO()
    book = xlsxwriter.Workbook(output, {'in_memory': True, 'strings_to_formulas': False, 'strings_to_urls': False})
    sheet = book.add_worksheet()
    for index, row in enumerate([header] + rows):
        for column, cell in enumerate(row):
            value = _text(cell)
            if isinstance(value, str) or value is None:
                sheet.write_string(index, column, value or '')
            else:
                sheet.write(index, column, value)
    book.close()
    return output.getvalue()
