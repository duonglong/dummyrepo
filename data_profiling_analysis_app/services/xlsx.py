"""XLSX writer of a report or snapshot: plain rows in, workbook bytes out, nothing stored."""
import io

import xlsxwriter

from odoo import fields

NOT_RATED = '-'

SHEET_SUMMARY = 'Summary'
SHEET_STATISTICS = 'Field statistics'
SHEET_VALUES = 'Values'
SHEET_FLAGGED = 'Flagged values'
SHEET_OUTDATED = 'Outdated records'


def _local_time(snapshot, moment):
    """A date and time as the person reading the file sees it."""
    return fields.Datetime.context_timestamp(snapshot, moment).strftime('%Y-%m-%d %H:%M') if moment else ''


def _summary_rows(snapshot):
    env = snapshot.env
    origins = dict(snapshot._fields['origin'].selection)
    rows = [
        (env._("Profile"), snapshot.profile_name),
        (env._("Model"), snapshot.model_label),
        (env._("Run time"), _local_time(snapshot, snapshot.run_date)),
        (env._("Origin"), origins[snapshot.origin]),
        (env._("Author"), snapshot.user_id.name or ''),
        (env._("Records profiled"), snapshot.record_count),
        (env._("Empty-value threshold (%)"), snapshot.empty_threshold),
        (env._("Outdated period (days)"), snapshot.outdated_days),
    ]
    if snapshot.has_log_access:
        rows.append((env._("Outdated records"), snapshot.outdated_count))
    return rows


def _statistics_header(env):
    return [
        env._("Field"), env._("Type"), env._("Records"), env._("Filled"), env._("Empty"),
        env._("Completeness (%)"), env._("Empty rate (%)"), env._("Distinct"), env._("Flags"),
        env._("Most common format"), env._("Usual range from"), env._("Usual range to"),
        env._("Minimum"), env._("Maximum"), env._("Sum"), env._("Average"), env._("Median"),
        env._("Standard deviation"), env._("Earliest"), env._("Latest"), env._("Span (days)"),
        env._("Unused values"),
    ]


def _statistics_row(line):
    if not line.is_rated:
        rated = [NOT_RATED] * 4
    else:
        rated = [line.filled_count, line.empty_count, line.completeness, line.empty_rate]
    numeric = line.field_type in ('integer', 'float', 'monetary') and line.is_rated
    dated = line.field_type in ('date', 'datetime') and line.is_rated and line.filled_count
    return [
        line.field_label, line.field_type, line.record_count, *rated, line.distinct_count,
        line.flag_labels or '', line.main_pattern or '',
        line.range_low if line.range_computed else '', line.range_high if line.range_computed else '',
        line.min_value if numeric else '', line.max_value if numeric else '', line.sum_value if numeric else '',
        line.mean_value if numeric else '', line.median_value if numeric else '',
        line.stddev_value if numeric else '',
        str(line.earliest_date) if dated else '', str(line.latest_date) if dated else '',
        line.span_days if dated else '', line.unused_values or '',
    ]


def _sheets(snapshot):
    """The five sheets as (name, header row, data rows)."""
    env = snapshot.env
    kinds = dict(env['dpa.snapshot.finding']._fields['kind'].selection)
    findings = snapshot.finding_ids
    flagged = findings.filtered(lambda finding: finding.kind != 'outdated')
    outdated = findings - flagged
    return [
        (SHEET_STATISTICS, _statistics_header(env), [_statistics_row(line) for line in snapshot.field_line_ids]),
        (SHEET_VALUES, [env._("Field"), env._("Value"), env._("Records"), env._("Share (%)")], [
            [line.field_label, NOT_RATED if value.is_empty else value.value_label or '', value.count, value.share]
            for line in snapshot.field_line_ids for value in line.value_ids]),
        (SHEET_FLAGGED, [env._("Field"), env._("Flag"), env._("Value"), env._("Record"), env._("Model"), env._("ID")], [
            [finding.field_line_id.field_label or '', kinds[finding.kind], finding.value_label or '',
             finding.record_name or '', finding.res_model, finding.res_id] for finding in flagged]),
        (SHEET_OUTDATED, [env._("Record"), env._("Model"), env._("ID"), env._("Last update"), env._("Days since update")], [
            [finding.record_name or '', finding.res_model, finding.res_id,
             _local_time(snapshot, finding.last_update), finding.days_since_update] for finding in outdated]),
    ]


def build(snapshot):
    """The workbook of a report or snapshot with every line it holds.

    Requirements: FR-DPA-0026
    """
    output = io.BytesIO()
    workbook = xlsxwriter.Workbook(output, {'in_memory': True})
    bold = workbook.add_format({'bold': True})
    summary = workbook.add_worksheet(SHEET_SUMMARY)
    for row, (label, value) in enumerate(_summary_rows(snapshot)):
        summary.write(row, 0, label, bold)
        summary.write(row, 1, value)
    for name, header, rows in _sheets(snapshot):
        sheet = workbook.add_worksheet(name)
        sheet.write_row(0, 0, header, bold)
        for number, row in enumerate(rows, start=1):
            sheet.write_row(number, 0, row)
    workbook.close()
    return output.getvalue()
