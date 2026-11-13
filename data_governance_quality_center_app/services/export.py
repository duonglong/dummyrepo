"""Building the audit export: the filter, the rows and the XLSX bytes."""
import io
from datetime import datetime, time, timedelta

from odoo.fields import Domain

COLUMNS = ['Time', 'Event', 'User', 'Company', 'Model', 'Record ID', 'Record', 'Fields', 'Values', 'Description']


def count_entries_before(env, date_before):
    """Count the audit entries older than the day ``date_before``.

    Requirements: FR-DGQ-0073
    """
    return env['data.quality.audit'].search_count([('date', '<', datetime.combine(date_before, time.min))])


def audit_domain(wizard):
    """Domain of the audit entries a export dialog asks for.

    Requirements: FR-DGQ-0074
    """
    domain = Domain.TRUE
    if wizard.date_from:
        domain &= Domain('date', '>=', datetime.combine(wizard.date_from, time.min))
    if wizard.date_to:
        domain &= Domain('date', '<', datetime.combine(wizard.date_to + timedelta(days=1), time.min))
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
        str(entry.date), labels[entry.event], entry.user_id.name or '', entry.company_id.name or '',
        entry.res_model or '', entry.res_id or '', entry.record_name or '', entry.field_names or '',
        str(entry.changes) if entry.changes else '', entry.description or '',
    ] for entry in entries]


def audit_xlsx(entries):
    """Return the XLSX bytes of the audit entries, all columns.

    Requirements: FR-DGQ-0074
    """
    import xlsxwriter  # noqa: PLC0415

    output = io.BytesIO()
    workbook = xlsxwriter.Workbook(output, {'in_memory': True})
    sheet = workbook.add_worksheet('Audit')
    for col, title in enumerate(COLUMNS):
        sheet.write(0, col, title)
    for row, values in enumerate(audit_rows(entries), start=1):
        for col, value in enumerate(values):
            sheet.write(row, col, value)
    workbook.close()
    return output.getvalue()
