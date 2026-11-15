"""The figures of the health page: queued, running and failed work, scheduled tasks, durations and stored volumes."""
from datetime import timedelta

from odoo import fields
from odoo.fields import Domain

from ..const import HEALTH_FAILED_DAYS, HEALTH_LAST_SCANS


def _job_models(env):
    """The models of the app that are background jobs."""
    mixin = env.registry['data.quality.job.mixin']
    return [
        env[name] for name, model in env.registry.items()
        if issubclass(model, mixin) and name != mixin._name and model._auto]


def _failed_runs(env, since):
    """The failed jobs of every kind since a moment, newest first, each with its reason and the record to open."""
    failed_domain = Domain('state', '=', 'failed') & (
        Domain('date_end', '>=', since) | (Domain('date_end', '=', False) & Domain('write_date', '>=', since)))
    runs = []
    for model in _job_models(env):
        for job in model.search(failed_domain):
            runs.append({
                'model': model._name, 'kind': model._description, 'id': job.id, 'name': job.display_name,
                'error': job.error_message or '',
                'date': fields.Datetime.to_string(job.date_end or job.write_date)})
    return sorted(runs, key=lambda run: run['date'], reverse=True)


def _scan_counts(env):
    """Scans by state: how many are queued, running and failed."""
    groups = env['data.quality.scan']._read_group(
        [('state', 'in', ['queued', 'running', 'failed'])], ['state'], ['__count'])
    counts = dict.fromkeys(('queued', 'running', 'failed'), 0)
    counts.update(dict(groups))
    return counts


def _last_scans(env):
    """The last finished scans with how long each took, in seconds."""
    scans = env['data.quality.scan'].search(
        [('state', '=', 'done'), ('date_start', '!=', False), ('date_end', '!=', False)],
        order='date_end desc, id desc', limit=HEALTH_LAST_SCANS)
    return [{
        'id': scan.id, 'name': scan.display_name,
        'seconds': int((scan.date_end - scan.date_start).total_seconds())} for scan in scans]


def _scheduled_tasks(env):
    """The last and next run of every scheduled action of the app."""
    crons = env['ir.cron'].search([('model_id.model', '=like', 'data.quality.%')], order='name')
    return [{
        'id': cron.id, 'name': cron.name, 'active': cron.active,
        'last_run': fields.Datetime.to_string(cron.lastcall) if cron.lastcall else False,
        'next_run': fields.Datetime.to_string(cron.nextcall) if cron.nextcall else False} for cron in crons]


def _last_cleanup(env):
    """What the cleanups of the last day it deleted something removed, added up, or None."""
    audit = env['data.quality.audit']
    last = audit.search([('event', '=', 'cleanup')], order='date desc, id desc', limit=1)
    if not last:
        return None
    day_start = fields.Datetime.to_datetime(last.date.date())
    deleted = {}
    for entry in audit.search([('event', '=', 'cleanup'), ('date', '>=', day_start)]):
        for name, values in (entry.changes or {}).items():
            deleted[name] = deleted.get(name, 0) + values[1]
    return {'date': fields.Datetime.to_string(last.date), 'deleted': deleted}


def health(env):
    """Everything the health page shows, read across all companies (the page is for administrators).

    Requirements: FR-DGQ-0079, FR-DGQ-0077
    """
    env = env(su=True)  # sudo: the health page is an administrator-wide view of the app's own work
    since = fields.Datetime.now() - timedelta(days=HEALTH_FAILED_DAYS)
    return {
        'scans': _scan_counts(env),
        'failed_days': HEALTH_FAILED_DAYS,
        'failed_runs': _failed_runs(env, since),
        'last_scans': _last_scans(env),
        'scheduled_tasks': _scheduled_tasks(env),
        'held': {
            'scan_lines': env['data.quality.scan.line'].search_count([]),
            'issues': env['data.quality.issue'].search_count([]),
            'snapshots': env['data.quality.score.snapshot'].search_count([]),
            'audit_entries': env['data.quality.audit'].search_count([]),
        },
        'last_cleanup': _last_cleanup(env),
    }
