"""The quality report: the figures of the dashboard for chosen companies and dates, and their XLSX file."""
import io
from datetime import timedelta

import xlsxwriter

from odoo.fields import Domain

from . import scoring


def _scored(totals):
    """Rows ``{key: [applicable weight, failed weight]}`` as ``(key, score)``, weights added up, never scores averaged."""
    return [(key, scoring.score(*weights)) for key, weights in totals.items()]


def _rule_rows(env, companies, end):
    """The latest score of every rule over the chosen companies, on or before ``end``: name, model and score."""
    Snapshot = env['data.quality.score.snapshot']
    domain = Domain('company_id', 'in', companies.ids) & Domain('rule_id', '!=', False)
    if end:
        domain &= Domain('date', '<', end)
    groups = Snapshot._read_group(domain, ['company_id', 'model_id', 'rule_id'], ['id:max'])
    totals = {}
    for snap in Snapshot.browse([latest for *_keys, latest in groups]):
        weights = totals.setdefault((snap.rule_id.name, snap.model_id.sudo().name), [0, 0])  # sudo: a viewer may not read ir.model
        weights[0] += snap.applicable_weight
        weights[1] += snap.failed_weight
    return [
        {'name': name, 'model': model, 'score': score}
        for (name, model), score in sorted(_scored(totals), key=lambda item: item[0])]


def _trend_rows(env, companies, start, end, period):
    """One score per period between the dates: the latest snapshot of each company and model in the period."""
    Snapshot = env['data.quality.score.snapshot']
    domain = Domain('company_id', 'in', companies.ids) & Domain('rule_id', '=', False)
    if start:
        domain &= Domain('date', '>=', start)
    if end:
        domain &= Domain('date', '<', end)
    groups = Snapshot._read_group(domain, [f'date:{period}', 'company_id', 'model_id'], ['id:max'])
    latest = Snapshot.browse([snapshot_id for *_keys, snapshot_id in groups])
    totals = {}
    for (day, _company, _model, snapshot_id) in groups:
        snap = latest.browse(snapshot_id)
        weights = totals.setdefault(day, [0, 0])
        weights[0] += snap.applicable_weight
        weights[1] += snap.failed_weight
    return [{'period': day.isoformat(), 'score': score} for day, score in sorted(_scored(totals))]


def report_data(env, company_ids, date_from, date_to, period):
    """The figures of the quality report, the ones the dashboard shows plus rules, trend, statuses and resolution time.

    The figures come from the same snapshots and issues as the dashboard, within the user's own
    access (the dashboard method refuses a user below viewer level).

    Requirements: FR-DGQ-0078
    """
    dashboard = env['data.quality.dashboard']
    figures = dashboard.get_figures(company_ids=company_ids, date=date_to)
    companies = dashboard._chosen_companies(company_ids)
    end = dashboard._end_of_day(date_to)
    start = dashboard._end_of_day(date_from - timedelta(days=1)) if date_from else None
    issue_domain = Domain('company_id', 'in', [False, *companies.ids])
    if end:
        issue_domain &= Domain('last_found', '<', end)
    Issue = env['data.quality.issue']
    labels = dict(Issue._fields['state']._description_selection(env))
    severities = dict(Issue._fields['severity']._description_selection(env))
    by_status = Issue._read_group(issue_domain, ['state'], ['__count'])
    resolved = issue_domain & Domain('state', '=', 'resolved')
    if start:
        resolved &= Domain('date_resolved', '>=', start)
    if end:
        resolved &= Domain('date_resolved', '<', end)
    (average,) = Issue._read_group(resolved, [], ['resolution_hours:avg'])[0]
    return {
        'companies': companies.mapped('name'),
        'date_from': date_from, 'date_to': date_to,
        'score': figures['score'],
        'open_issues': figures['open_issues'],
        'by_company': figures['by_company'],
        'by_model': figures['by_model'],
        'by_rule': _rule_rows(env, companies, end),
        'trend': _trend_rows(env, companies, start, end, period),
        'issues_by_severity': [
            {'severity': severities[severity], 'count': count} for severity, count in figures['issues_by_severity'].items()],
        'issues_by_status': [{'state': labels[state], 'count': count} for state, count in by_status],
        'average_resolution_hours': average or 0.0,
    }


def _table(workbook, sheet, row, title, header, rows):
    """Write a titled table from a row of a sheet and return the first free row after it."""
    sheet.write_string(row, 0, title, workbook.add_format({'bold': True}))
    for col, name in enumerate(header):
        sheet.write_string(row + 1, col, name)
    for offset, values in enumerate(rows, start=2):
        for col, value in enumerate(values):
            if isinstance(value, (int, float)):
                sheet.write_number(row + offset, col, value)
            else:
                sheet.write_string(row + offset, col, value or '')
    return row + len(rows) + 3


def _rounded(score):
    """A score for a cell: one decimal, empty when no rule applied."""
    return None if score is None else round(score, 1)


def report_xlsx(env, data):
    """The XLSX bytes of the report: one sheet with a table per figure.

    Requirements: FR-DGQ-0078
    """
    output = io.BytesIO()
    workbook = xlsxwriter.Workbook(
        output, {'in_memory': True, 'strings_to_formulas': False, 'strings_to_urls': False})
    sheet = workbook.add_worksheet(env._("Quality report"))
    tables = [
        (env._("Summary"),
         [env._("Companies"), env._("Score"), env._("Open issues"), env._("Average resolution (hours)")],
         [[", ".join(data['companies']), _rounded(data['score']), data['open_issues'],
           round(data['average_resolution_hours'], 1)]]),
        (env._("Scores by company"), [env._("Company"), env._("Score")],
         [[row['name'], _rounded(row['score'])] for row in data['by_company']]),
        (env._("Scores by model"), [env._("Model"), env._("Score")],
         [[row['name'], _rounded(row['score'])] for row in data['by_model']]),
        (env._("Scores by rule"), [env._("Rule"), env._("Model"), env._("Score")],
         [[row['name'], row['model'], _rounded(row['score'])] for row in data['by_rule']]),
        (env._("Trend"), [env._("Period"), env._("Score")],
         [[row['period'], _rounded(row['score'])] for row in data['trend']]),
        (env._("Open issues by severity"), [env._("Severity"), env._("Issues")],
         [[row['severity'], row['count']] for row in data['issues_by_severity']]),
        (env._("Issues by status"), [env._("Status"), env._("Issues")],
         [[row['state'], row['count']] for row in data['issues_by_status']]),
    ]
    row = 0
    for title, header, rows in tables:
        row = _table(workbook, sheet, row, title, header, rows)
    workbook.close()
    return output.getvalue()
