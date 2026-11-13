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

STALE_MESSAGE = "Stopped: server restart or timeout."


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
            'error_message': env._(STALE_MESSAGE),
            'date_end': fields.Datetime.now(),
        })
    return stale


def take_next_job(env, model_name):
    """Lock and return the oldest queued job of a kind, or an empty recordset.

    Requirements: NFR-DGQ-0002
    """
    model = env[model_name].sudo()  # jobs are written by the app whoever asked for them
    env.cr.execute(SQL(
        "SELECT id FROM %s WHERE state = 'queued' "
        "ORDER BY date_queued, id LIMIT 1 FOR UPDATE SKIP LOCKED",
        SQL.identifier(model._table),
    ))
    row = env.cr.fetchone()
    return model.browse(row[0]) if row else model.browse()


def run_next_job(env, model_name):
    """Run the oldest queued job of a kind in committed batches.

    The job stops when the time left of the scheduled action is shorter than its
    last batch: it goes back to queued with its checkpoint and the scheduled action
    runs again because work remains. A batch that raises is rolled back and the job
    is set failed with the error, in its own committed write.

    Requirements: NFR-DGQ-0002, NFR-DGQ-0003, NFR-DGQ-0005
    """
    fail_stale_jobs(env, model_name)
    job = take_next_job(env, model_name)
    if not job:
        return job
    job_id = job.id
    job.write({
        'state': 'running',
        'date_start': job.date_start or fields.Datetime.now(),
        'heartbeat': fields.Datetime.now(),
    })
    while True:
        started = time.monotonic()
        try:
            processed, remaining = job._dgq_run_batch()
        except Exception as error:  # noqa: BLE001 (any batch failure fails the job, never the runner)
            _logger.exception("dgq %s job %s failed", model_name, job_id)
            env['ir.cron']._rollback_progress()
            env.invalidate_all()
            job = env[model_name].sudo().browse(job_id)  # root writes the failure of any user's job
            job.write({
                'state': 'failed',
                'error_message': str(error),
                'date_end': fields.Datetime.now(),
            })
            env['ir.cron']._commit_progress()
            return job
        job._dgq_record_batch(processed, remaining)
        if not remaining:
            env['ir.cron']._commit_progress(processed, remaining=0)
            return job
        left = env['ir.cron']._commit_progress(processed, remaining=remaining)
        if left < time.monotonic() - started:
            job.write({'state': 'queued', 'date_queued': fields.Datetime.now()})
            env['ir.cron']._commit_progress(0, remaining=remaining)
            return job
