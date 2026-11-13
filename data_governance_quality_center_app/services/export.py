"""Building the audit export: the filter, the rows and the XLSX bytes."""
import io
from datetime import datetime, time, timedelta

import pytz
import xlsxwriter

from odoo import fields
from odoo.exceptions import UserError
from odoo.fields import Domain

COLUMNS = ['Time', 'Event', 'User', 'Company', 'Model', 'Record ID', 'Record', 'Fields', 'Values', 'Description']


def day_start_utc(env, day):
    """Return the UTC moment the day ``day`` starts in the user's time zone (naive, as stored)."""
    tz = pytz.timezone(env.context.get('tz') or env.user.tz or 'UTC')
    return tz.localize(datetime.combine(day, time.min)).astimezone(pytz.utc).replace(tzinfo=None)


def count_entries_before(env, date_before):
    """Count the audit entries older than the day ``date_before``.

    Requirements: FR-DGQ-0073
    """
    return env['data.quality.audit'].search_count([('date', '<', day_start_utc(env, date_before))])


def audit_domain(wizard):
    """Domain of the audit entries an export dialog asks for.

    Requirements: FR-DGQ-0074
    """
    domain = Domain.TRUE
    if wizard.date_from:
        domain &= Domain('date', '>=', day_start_utc(wizard.env, wizard.date_from))
    if wizard.date_to:
        domain &= Domain('date', '<', day_start_utc(wizard.env, wizard.date_to + timedelta(days=1)))
    if wizard.event:
        domain &= Domain('event', '=', wizard.event)
    if wizard.user_ids:
        domain &= Domain('user_id', 'in', wizard.user_ids.ids)
    if wizard.model_ids:
        domain &= Domain('res_model', 'in', wizard.model_ids.mapped('model'))
    if wizard.company_ids:
        domain &= Domain('company_id', 'in', wizard.company_ids.ids)
    return domain


def audit_rows(entries):
    """Rows of the export, one list per entry, in the order of ``COLUMNS``.

    Requirements: FR-DGQ-0074
    """
    labels = dict(entries._fields['event']._description_selection(entries.env))
    return [[
        fields.Datetime.context_timestamp(entry, entry.date).strftime('%Y-%m-%d %H:%M:%S'), labels[entry.event], entry.user_id.name or '', entry.company_id.name or '',
        entry.res_model or '', entry.res_id or '', entry.record_name or '', entry.field_names or '',
        str(entry.changes) if entry.changes else '', entry.description or '',
    ] for entry in entries]


def audit_xlsx(entries):
    """Return the XLSX bytes of the audit entries, all columns.

    Requirements: FR-DGQ-0074
    """
    rows = audit_rows(entries)
    output = io.BytesIO()
    workbook = xlsxwriter.Workbook(
        output, {'in_memory': True, 'strings_to_formulas': False, 'strings_to_urls': False})
    sheet = workbook.add_worksheet('Audit')
    if len(rows) + 1 > sheet.xls_rowmax:
        raise UserError(entries.env._(
            "Too many entries for one XLSX sheet (%(count)s). Narrow the filters.", count=len(rows)))
    for col, title in enumerate(COLUMNS):
        sheet.write_string(0, col, title)
    for row, values in enumerate(rows, start=1):
        for col, value in enumerate(values):
            if isinstance(value, int):
                sheet.write_number(row, col, value)
                continue
            if len(value) > sheet.xls_strmax:
                value = entries.env._("[Text too long for a spreadsheet cell: see the PDF or the database]")
            sheet.write_string(row, col, value)
    workbook.close()
    return output.getvalue()
