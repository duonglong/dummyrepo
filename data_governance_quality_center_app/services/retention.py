"""The daily cleanup: old scan details, import files and closed issues leave; totals, snapshots and audit stay."""
import logging
from datetime import timedelta

from odoo import fields
from odoo.fields import Domain

from ..const import (
    ISSUE_RETENTION_DAYS, ISSUE_RETENTION_PARAMETER, JOB_BATCH_SIZE, SCAN_LINE_RETENTION_DAYS,
    SCAN_LINE_RETENTION_PARAMETER)
from . import audit

_logger = logging.getLogger(__name__)


def _retention_limits(env, now):
    """The moments before which scan details, and closed issues, are old enough to delete."""
    params = env['ir.config_parameter'].sudo()  # sudo: the cleanup runs for the system, whoever calls it
    return (
        now - timedelta(days=params.get_int(SCAN_LINE_RETENTION_PARAMETER, SCAN_LINE_RETENTION_DAYS)),
        now - timedelta(days=params.get_int(ISSUE_RETENTION_PARAMETER, ISSUE_RETENTION_DAYS)))


def _delete_in_batches(env, records, domain):
    """Delete the records matching the domain, one committed batch at a time; return how many."""
    deleted = 0
    while True:
        batch = records.search(domain, limit=JOB_BATCH_SIZE)
        if not batch:
            return deleted
        batch.unlink()
        deleted += len(batch)
        env['ir.cron']._commit_progress(len(batch))


def _clear_files(env, sessions, domain):
    """Empty the file of the import sessions matching the domain, in committed batches; return how many."""
    cleared = 0
    while True:
        batch = sessions.search(domain, limit=JOB_BATCH_SIZE)
        if not batch:
            return cleared
        batch.write({'file': False})
        cleared += len(batch)
        env['ir.cron']._commit_progress(len(batch))


def purge(env):
    """Delete what is older than the retention periods and record what was deleted.

    Scan lines and import files follow the scan detail retention, Resolved and Ignored issues the
    issue retention (BR-DGQ-0013). Scan totals, snapshots and audit entries are never touched.

    Requirements: FR-DGQ-0077
    """
    env = env(su=True)  # sudo: the cleanup deletes the app's own results for every user and company
    now = fields.Datetime.now()
    detail_limit, issue_limit = _retention_limits(env, now)
    closed = Domain('state', 'in', ['resolved', 'ignored']) & (
        Domain('date_resolved', '<', issue_limit)
        | (Domain('date_resolved', '=', False) & Domain('write_date', '<', issue_limit)))
    counts = {
        'scan_lines': _delete_in_batches(
            env, env['data.quality.scan.line'], [('create_date', '<', detail_limit)]),
        'import_files': _clear_files(
            env, env['data.quality.import.session'], [('create_date', '<', detail_limit), ('file', '!=', False)]),
        'issues': _delete_in_batches(env, env['data.quality.issue'], closed),
    }
    if any(counts.values()):
        _logger.info("dgq cleanup: deleted %s", counts)
        audit.log(
            env, 'cleanup', changes={name: [False, count] for name, count in counts.items()},
            description=env._(
                "Cleanup deleted %(lines)s scan lines, %(files)s import files and %(issues)s closed issues.",
                lines=counts['scan_lines'], files=counts['import_files'], issues=counts['issues']))
    return counts
