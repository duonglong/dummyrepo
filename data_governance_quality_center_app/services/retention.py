"""The daily cleanup: old scan details, import files and closed issues leave; totals, snapshots and audit stay."""
import logging
import time
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


def _in_batches(env, name, label, records, domain, apply):
    """Apply ``apply`` to the records matching the domain, one audited, committed batch at a time.

    Each batch is recorded in the audit trail in the same transaction as its change, so a committed
    batch is never unrecorded. Returns ``(count, finished)``; it is not finished when the time left
    for the scheduled action is shorter than the last batch took, so the native runner calls again.
    """
    count = 0
    while True:
        started = time.monotonic()
        batch = records.search(domain, limit=JOB_BATCH_SIZE)
        if not batch:
            return count, True
        apply(batch)
        count += len(batch)
        audit.log(
            env, 'cleanup', changes={name: [False, len(batch)]},
            description=env._("Cleanup removed %(count)s %(what)s.", count=len(batch), what=label))
        left = env['ir.cron']._commit_progress(len(batch), remaining=1)
        if left < time.monotonic() - started:
            return count, False


def purge(env):
    """Delete what is older than the retention periods and record what was deleted, batch by batch.

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
    steps = (
        ('scan_lines', env._("scan lines"), env['data.quality.scan.line'],
         [('create_date', '<', detail_limit)], lambda batch: batch.unlink()),
        ('import_files', env._("import files"), env['data.quality.import.session'],
         [('create_date', '<', detail_limit), ('file', '!=', False)], lambda batch: batch.write({'file': False})),
        ('issues', env._("closed issues"), env['data.quality.issue'], closed, lambda batch: batch.unlink()),
    )
    counts = dict.fromkeys((step[0] for step in steps), 0)
    for name, label, records, domain, apply in steps:
        counts[name], finished = _in_batches(env, name, label, records, domain, apply)
        if not finished:
            break
    else:
        env['ir.cron']._commit_progress(0, remaining=0)
    if any(counts.values()):
        _logger.info("dgq cleanup: deleted %s", counts)
    return counts
