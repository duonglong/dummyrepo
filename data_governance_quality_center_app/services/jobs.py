"""Runner of the background jobs: committed batches with a stored checkpoint.

The runner is called by the scheduled action of one job kind. It takes the oldest
queued job, processes it batch by batch and commits after each batch, so a lock is
never held longer than one batch and a restart loses at most the batch in progress.
"""
import logging
import time
from datetime import timedelta

from odoo import fields
from odoo.tools import SQL

from ..const import STALE_JOB_MINUTES

_logger = logging.getLogger(__name__)

def fail_stale_jobs(env, model_name):
    """Set failed the running jobs of a kind whose heartbeat stopped.

    Requirements: NFR-DGQ-0005
    """
    limit = fields.Datetime.now() - timedelta(minutes=STALE_JOB_MINUTES)
    # the runner is root: it fails the jobs of every user
    stale = env[model_name].sudo().search([
        ('state', '=', 'running'), ('heartbeat', '<', limit),
    ])
    if stale:
        _logger.warning("dgq %s: %s stale job(s) set failed", model_name, len(stale))
        stale.write({
            'state': 'failed',
            'error_message': env._("Stopped: server restart or timeout."),
            'date_end': fields.Datetime.now(),
        })
    return stale


def take_next_job(env, model_name):
    """Lock and return the oldest queued job of a kind, or an empty recordset.

    Requirements: NFR-DGQ-0002
    """
    model = env[model_name].sudo()  # jobs are written by the app whoever asked for them
    model.flush_model(['state', 'date_queued'])
    env.cr.execute(SQL(
        "SELECT id FROM %s WHERE state = 'queued' "
        "ORDER BY date_queued, id LIMIT 1 FOR UPDATE SKIP LOCKED",
        SQL.identifier(model._table),
    ))
    row = env.cr.fetchone()
    return model.browse(row[0]) if row else model.browse()


def lock_if_running(job):
    """Lock the job row and tell whether it is still running.

    A user may cancel a job between two batches. The lock is held until the next
    commit, so the runner either sees the cancel or the cancel waits for the batch.

    Requirements: NFR-DGQ-0005
    """
    job.flush_model(['state'])
    job.env.cr.execute(SQL(
        "SELECT 1 FROM %s WHERE id = %s AND state = 'running' FOR UPDATE",
        SQL.identifier(job._table), job.id,
    ))
    running = bool(job.env.cr.fetchone())
    if not running:
        job.invalidate_recordset(['state'])
    return running


def queued_count(env, model_name):
    """Number of queued jobs of a kind: the work that remains for the scheduled action."""
    return env[model_name].sudo().search_count([('state', '=', 'queued')])


def _fail_job(env, model_name, job_id, error):
    """Roll back the batch in progress and set the job failed, in its own commit."""
    _logger.exception("dgq %s job %s failed", model_name, job_id)
    cron = env['ir.cron']
    cron._rollback_progress()
    env.invalidate_all()
    job = env[model_name].sudo().browse(job_id)  # root writes the failure of any user's job
    if lock_if_running(job):
        job.write({
            'state': 'failed',
            # the error text may quote record values: keep only its kind
            'error_message': env._("A batch failed (%s). See the server log.", type(error).__name__),
            'date_end': fields.Datetime.now(),
        })
    cron._commit_progress(0, remaining=queued_count(env, model_name))
    return job


def run_next_job(env, model_name):
    """Run the oldest queued job of a kind in committed batches.

    The job stops when the time left of the scheduled action is shorter than its
    last batch: it goes back to queued with its checkpoint. A batch that raises is
    rolled back and the job is set failed, in its own committed write. A job
    cancelled between batches stays cancelled. The remaining work reported to the
    scheduled action counts the queued jobs, so it runs again while any is waiting.

    Requirements: NFR-DGQ-0002, NFR-DGQ-0003, NFR-DGQ-0005
    """
    cron = env['ir.cron']
    fail_stale_jobs(env, model_name)
    job = take_next_job(env, model_name)
    if not job:
        cron._commit_progress()  # keep the stale marks
        return job
    job_id = job.id
    job.write({
        'state': 'running',
        'date_start': job.date_start or fields.Datetime.now(),
        'heartbeat': fields.Datetime.now(),
    })
    cron._commit_progress(0, remaining=queued_count(env, model_name) + 1)
    while lock_if_running(job):
        started = time.monotonic()
        try:
            processed, remaining = job._dgq_run_batch()
            job._dgq_record_batch(processed, remaining)
        except Exception as error:  # noqa: BLE001 (any batch failure fails the job, never the runner)
            return _fail_job(env, model_name, job_id, error)
        others = queued_count(env, model_name)
        left = cron._commit_progress(processed, remaining=remaining + others)
        if not remaining:
            return job
        if left < time.monotonic() - started and lock_if_running(job):
            job.write({'state': 'queued', 'date_queued': fields.Datetime.now()})
            cron._commit_progress(0, remaining=remaining + others)
            return job
    cron._commit_progress(0, remaining=queued_count(env, model_name))
    return job
