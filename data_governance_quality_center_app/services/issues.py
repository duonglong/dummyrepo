"""Issues of a scan batch: raise, update, resolve and reopen, one issue per rule and record.

A scan calls these functions with the outcomes of one batch; they write as the app (sudo), since
the person who started the scan may not create issues.
"""
from odoo import fields

from ..const import ISSUE_OPEN_STATES
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
        new_values = []
        for record_id, outcome in failing.items():
            issue = found.get(record_id)
            values = {
                'scan_id': scan.id, 'rule_version': rule.version, 'severity': rule.severity,
                'last_found': now, 'explanation_code': outcome.code, 'explanation_params': outcome.params}
            if not issue:
                new_values.append(dict(
                    values, rule_id=rule.id, res_model=records._name, res_id=record_id,
                    company_id=_company_of(records.browse(record_id)), first_scan_id=scan.id,
                    first_found=now, record_name=records.browse(record_id).display_name))
                continue
            if issue.state == 'resolved':
                values['state'] = 'new'
                notes[issue] = env._("Reopened by scan %s: the record fails the rule again.", scan.name)
            issue.write(values)
            updated += 1
        created += len(Issue.with_context(mail_create_nolog=True, tracking_disable=True).create(new_values))
        open_issues = Issue.search([
            ('rule_id', '=', rule.id), ('res_model', '=', records._name),
            ('res_id', 'in', records.ids), ('state', 'in', ISSUE_OPEN_STATES)])
        for issue in open_issues.filtered(lambda issue: issue.res_id not in failing):
            issue.write({'state': 'resolved', 'resolved_by_scan': True, 'scan_id': scan.id})
            notes[issue] = env._("Resolved by scan %s: the record passes the rule or the rule no longer applies.", scan.name)
            resolved += 1
    _log(notes)
    return created, updated, resolved


def resolve_deleted(env, scan, rules, model_name):
    """Resolve the open issues of records of the model that no longer exist; return how many.

    Requirements: FR-DGQ-0023
    """
    Issue = env['data.quality.issue'].sudo()  # sudo: the app writes issues, whoever started the scan
    issues = Issue.search([
        ('rule_id', 'in', rules.ids), ('res_model', '=', model_name), ('state', 'in', ISSUE_OPEN_STATES),
        ('company_id', 'in', [False, *scan.company_ids.ids])])
    existing = env[model_name].sudo().with_context(active_test=False).browse(issues.mapped('res_id')).exists().ids
    gone = issues.filtered(lambda issue: issue.res_id not in existing)
    notes = {}
    for issue in gone:
        issue.write({'state': 'resolved', 'resolved_by_scan': True, 'scan_id': scan.id})
        notes[issue] = env._("Resolved by scan %s: the record no longer exists.", scan.name)
    _log(notes)
    return len(gone)


def _company_of(record):
    return 'company_id' in record._fields and record.company_id.id or False


def _log(notes):
    """Leave the note of each issue in its history."""
    for issue, body in notes.items():
        issue._message_log(body=body)
