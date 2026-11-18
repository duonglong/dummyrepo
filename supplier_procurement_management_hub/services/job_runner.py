"""Running a scheduled job of the app: one run at a time, logged, one bad record never stops the rest.

Requirements: FR-SPM-0096, FR-SPM-0097, NFR-SPM-0007
"""
import logging

from odoo import fields, modules

_logger = logging.getLogger(__name__)

BATCH_SIZE = 200


def _acquire(env, job, trigger):
    """Return a new Running run of ``job``, or a Skipped run when another run is going.

    Requirements: FR-SPM-0097
    The session advisory lock is held until the run ends (see ``_release``), so two starts
    at the same moment never both go on, whatever their transaction snapshot shows; the
    Running row covers a run seen as going by its row.
    """
    runs = env['procurement.job.run']
    env.cr.execute("SELECT pg_try_advisory_lock(hashtextextended(%s, 0))", [_lock_key(job)])
    locked = env.cr.fetchone()[0]
    now = fields.Datetime.now()
    values = {'job_id': job.id, 'trigger': trigger, 'date_start': now}
    if locked and not runs.search_count([('job_id', '=', job.id), ('state', '=', 'running')]):
        return runs.create(dict(values, state='running'))
    if locked:
        _release(env, job)
    return runs.create(dict(values, state='skipped', date_end=now))


def _lock_key(job):
    return 'spm_job:%s' % job.code


def _release(env, job):
    """Release the session lock taken by ``_acquire``."""
    env.cr.execute("SELECT pg_advisory_unlock(hashtextextended(%s, 0))", [_lock_key(job)])


def _commit(env, processed=0, remaining=None):
    """Commit the work done so far and report the progress to the scheduler.

    Only inside the scheduler's loop (ADR-SPM-007): Run now stays in its request transaction,
    and a test, where a commit is forbidden, never commits.
    """
    if env.context.get('ir_cron_progress_id') and not modules.module.current_test:
        env['ir.cron']._commit_progress(processed, remaining=remaining)


def _error_entry(record, error):
    """Return the log entry of a record that failed."""
    return {'model': record._name, 'id': record.id, 'name': record.display_name, 'message': str(error)}


def _process_batch(env, job, batch):
    """Process the records of one batch, each in its own savepoint; return (sent, errors)."""
    process = getattr(job, '_spm_process_%s' % job.code)
    sent, errors = 0, []
    for record in batch:
        try:
            with env.cr.savepoint():
                sent += process(record)
        except Exception as error:  # noqa: BLE001 - logged below with the record, the loop goes on
            _logger.warning("Job %s: record %s,%s failed: %s", job.code, record._name, record.id, error)
            errors.append(_error_entry(record, error))
    return sent, errors


def execute(env, job, trigger='scheduled'):
    """Run ``job`` and return its run record (done, failed or skipped).

    Requirements: FR-SPM-0096, FR-SPM-0097, NFR-SPM-0007
    The caller passes an elevated environment after its own role check. The job
    class provides ``_spm_collect_<code>()`` (the records due) and
    ``_spm_process_<code>(record)`` (returns the notifications sent). A record
    that fails is rolled back, logged with its id and tried again next run.
    """
    run = _acquire(env, job, trigger)
    if run.state == 'skipped':
        _commit(env)
        return run
    try:
        return _run(env, job, run)
    finally:
        _release(env, job)


def _run(env, job, run):
    """Process the records due of ``job`` into ``run`` and close it."""
    _commit(env)  # the Running row is now visible to any other start
    processed = sent = 0
    errors = []
    try:
        records = getattr(job, '_spm_collect_%s' % job.code)()
        for start in range(0, len(records), BATCH_SIZE):
            batch = records[start:start + BATCH_SIZE]
            batch_sent, batch_errors = _process_batch(env, job, batch)
            processed += len(batch)
            sent += batch_sent
            errors += batch_errors
            run.write({'processed_count': processed, 'sent_count': sent, 'errors': errors})
            _commit(env, len(batch), len(records) - start - len(batch))
    except Exception as error:  # noqa: BLE001 - the run is closed as failed, then the failure is logged
        _logger.exception("Job %s failed", job.code)
        run.write({'state': 'failed', 'date_end': fields.Datetime.now(), 'processed_count': processed,
                   'sent_count': sent, 'errors': errors + [{'message': str(error)}]})
        _commit(env)
        return run
    run.write({'state': 'done', 'date_end': fields.Datetime.now()})
    _commit(env)
    return run
