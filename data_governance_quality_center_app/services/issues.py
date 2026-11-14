"""Issues of a scan batch: raise, update, resolve and reopen, one issue per rule and record.

A scan calls these functions with the outcomes of one batch; they write as the app (sudo), since
the person who started the scan may not create issues.
"""
import json
from collections import defaultdict

from odoo.tools import SQL

from ..const import ISSUE_OPEN_STATES, JOB_BATCH_SIZE
from . import validation


def sync_batch(env, scan, rules, records, results, now):
    """Bring the issues of the batch's records in line with the scan; return (created, updated, resolved).

    ``results`` maps each rule to its outcomes by (record id, rule id). A failing record gets its
    issue created, updated or reopened; a record with an open issue that no longer fails, or that
    the rule no longer applies to, has the issue resolved.

    Requirements: BR-DGQ-0003, FR-DGQ-0021, FR-DGQ-0022, FR-DGQ-0023
    """
    Issue = env['data.quality.issue'].sudo()  # sudo: the app writes issues, whoever started the scan
    created = updated = resolved = 0
    notes = {}
    for rule in rules:
        failing = {
            record_id: outcome for (record_id, _rule_id), outcome in results[rule].items()
            if outcome.status == validation.FAIL}
        found = {
            issue.res_id: issue for issue in Issue.search([
                ('rule_id', '=', rule.id), ('res_model', '=', records._name), ('res_id', 'in', list(failing))])}
        shared = {'scan_id': scan.id, 'rule_version': rule.version, 'severity': rule.severity, 'last_found': now}
        new_values = []
        refreshed = defaultdict(lambda: Issue)
        for record_id, outcome in failing.items():
            issue = found.get(record_id)
            if not issue:
                new_values.append({
                    **shared, 'explanation_code': outcome.code, 'explanation_params': outcome.params,
                    'rule_id': rule.id, 'res_model': records._name, 'res_id': record_id,
                    'company_id': _company_of(records.browse(record_id)), 'first_scan_id': scan.id,
                    'first_found': now, 'record_name': records.browse(record_id).display_name})
                continue
            reopen = issue.state == 'resolved'
            if reopen:
                notes[issue] = env._("Reopened by scan %s: the record fails the rule again.", scan.name)
            refreshed[(outcome.code, json.dumps(outcome.params, sort_keys=True, default=str), reopen)] |= issue
            updated += 1
        for (code, params, reopen), issues in refreshed.items():
            issues.write({
                **shared, 'explanation_code': code, 'explanation_params': json.loads(params),
                **({'state': 'new'} if reopen else {})})
        created += len(Issue.with_context(mail_create_nolog=True, tracking_disable=True).create(new_values))
        open_issues = Issue.search([
            ('rule_id', '=', rule.id), ('res_model', '=', records._name),
            ('res_id', 'in', records.ids), ('state', 'in', ISSUE_OPEN_STATES)])
        closing = open_issues.filtered(lambda issue: issue.res_id not in failing)
        closing.write({'state': 'resolved', 'resolved_by_scan': True, 'scan_id': scan.id})
        for issue in closing:
            notes[issue] = env._(
                "Resolved by scan %s: the record passes the rule or the rule no longer applies.", scan.name)
        resolved += len(closing)
    _log(notes)
    return created, updated, resolved


def resolve_deleted(env, scan, rules, model_name):
    """Resolve the open issues of records of the model that no longer exist; return how many.

    One NOT EXISTS query finds a chunk of them, so no more than a batch is read at a time.

    Requirements: FR-DGQ-0023
    """
    Issue = env['data.quality.issue'].sudo()  # sudo: the app writes issues, whoever started the scan
    table = env[model_name]._table
    resolved = 0
    while True:
        Issue.flush_model()
        env.cr.execute(SQL(
            """
            SELECT issue.id FROM %(issues)s issue
             WHERE issue.rule_id = ANY(%(rules)s) AND issue.res_model = %(model)s
               AND issue.state = ANY(%(states)s)
               AND (issue.company_id IS NULL OR issue.company_id = ANY(%(companies)s))
               AND NOT EXISTS (SELECT 1 FROM %(records)s record WHERE record.id = issue.res_id)
             ORDER BY issue.id LIMIT %(limit)s
            """,
            issues=SQL.identifier(Issue._table), rules=rules.ids, model=model_name,
            states=list(ISSUE_OPEN_STATES), companies=scan.company_ids.ids,
            records=SQL.identifier(table), limit=JOB_BATCH_SIZE))
        gone = Issue.browse(row[0] for row in env.cr.fetchall())
        if not gone:
            return resolved
        gone.write({'state': 'resolved', 'resolved_by_scan': True, 'scan_id': scan.id})
        gone._message_log_batch(bodies={
            issue.id: env._("Resolved by scan %s: the record no longer exists.", scan.name) for issue in gone})
        resolved += len(gone)


def _company_of(record):
    return 'company_id' in record._fields and record.company_id.id or False


def _log(notes):
    """Leave the note of each issue in its history."""
    for issue, body in notes.items():
        issue._message_log(body=body)
