"""Check B7: scheduled actions that fail, run late, run too long or share a model.

Requirements: FR-OPA-0022, FR-OPA-0023, FR-OPA-0024, FR-OPA-0025, BR-OPA-0005
"""
from dateutil.relativedelta import relativedelta

from odoo import _, fields

from .base import Check, register

MIN_CRONS_PER_MODEL = 2


@register
class CronCheck(Check):
    """Reads every active scheduled action.

    Requirements: FR-OPA-0022, FR-OPA-0023, FR-OPA-0024, FR-OPA-0025
    """

    code = 'B7'

    def run(self, ctx):
        """Return the failure, late, runtime and shared findings of the active scheduled actions.

        Requirements: FR-OPA-0022, FR-OPA-0023, FR-OPA-0024, FR-OPA-0025
        """
        # scheduled actions are read for the audit whatever the rights of the user who started it
        crons = ctx.env['ir.cron'].sudo().search([])
        now = fields.Datetime.now()
        return (
            self._failures(crons, ctx.thresholds['failures'])
            + self._late(crons, ctx.thresholds['lateness'], now)
            + self._runtimes(ctx.env, crons)
            + self._shared(crons)
        )

    def _cron_values(self, cron, kind, message, **values):
        return dict(
            values, kind=kind, message=message, cron_id=cron.id,
            model_id=cron.ir_actions_server_id.model_id.id,
            identity_key=f'B7:{kind}:{cron.id}',
        )

    def _failures(self, crons, limit):
        """Requirements: FR-OPA-0022"""
        return [
            self._cron_values(
                cron, 'failure',
                _('Scheduled action "%(name)s" failed %(count)s times in a row.',
                  name=cron.cron_name, count=cron.failure_count),
                found_value=cron.failure_count, threshold_value=limit,
                code_suggestion=_('Read the error log of this scheduled action and fix the cause of the failure.'),
                threshold_label=_('Consecutive failures'),
                evidence={
                    'failure_count': cron.failure_count,
                    'first_failure_date': fields.Datetime.to_string(cron.first_failure_date),
                    'model': cron.ir_actions_server_id.model_id.model,
                })
            for cron in crons.filtered(lambda cron: cron.failure_count >= limit)
        ]

    def _late(self, crons, limit_hours, now):
        """Requirements: FR-OPA-0023"""
        findings = []
        for cron in crons:
            hours_late = (now - cron.nextcall).total_seconds() / 3600
            if hours_late > limit_hours:
                findings.append(self._cron_values(
                    cron, 'late',
                    _('Scheduled action "%(name)s" is %(hours).1f hours late.',
                      name=cron.cron_name, hours=hours_late),
                    found_value=hours_late, threshold_value=limit_hours,
                    code_suggestion=_('Check the scheduler load or a stuck run; split heavy work into triggered batches.'),
                    threshold_label=_('Lateness (hours)'),
                    evidence={'planned': fields.Datetime.to_string(cron.nextcall), 'hours_late': hours_late}))
        return findings

    def _runtimes(self, env, crons):
        """Requirements: FR-OPA-0024"""
        Progress = env['ir.cron.progress'].sudo()  # progress records are internal to the scheduler
        latest_ids = [latest for _cron, latest in Progress._read_group(
            [('cron_id', 'in', crons.ids)], ['cron_id'], ['id:max'])]
        findings = []
        for progress in Progress.browse(latest_ids):
            cron = progress.cron_id
            ran = (progress.write_date - progress.create_date).total_seconds() / 60
            interval = self._interval_minutes(cron, progress.create_date)
            if ran > interval:
                findings.append(self._cron_values(
                    cron, 'runtime',
                    _('Scheduled action "%(name)s" ran %(ran).1f minutes against an interval of %(interval).1f.',
                      name=cron.cron_name, ran=ran, interval=interval),
                    found_value=ran, threshold_value=interval,
                    code_suggestion=_('Process in batches with saved progress, or use triggers instead of one long run.'),
                    threshold_label=_('Repeat interval (minutes)'),
                    evidence={'run_minutes': ran, 'interval_minutes': interval}))
        return findings

    def _interval_minutes(self, cron, start):
        """The repeat interval of a scheduled action, in minutes, counted from `start`."""
        end = start + relativedelta(**{cron.interval_type: cron.interval_number})
        return (end - start).total_seconds() / 60

    def _shared(self, crons):
        """Requirements: FR-OPA-0025"""
        by_model = crons.grouped(lambda cron: cron.ir_actions_server_id.model_id)
        findings = []
        for model, model_crons in by_model.items():
            if len(model_crons) < MIN_CRONS_PER_MODEL:
                continue
            listing = [{
                'cron_id': cron.id, 'name': cron.cron_name,
                'interval': f'{cron.interval_number} {cron.interval_type}',
            } for cron in model_crons]
            names = ', '.join(f'{item["name"]} ({item["interval"]})' for item in listing)
            findings.append({
                'kind': 'shared', 'model_id': model.id,
                'identity_key': f'B7:shared:{model.model}',
                'message': _('Scheduled actions %(names)s all run on model %(model)s.', names=names, model=model.model),
                'code_suggestion': _('Stagger the timing of these scheduled actions so they do not lock each other.'),
                'found_value': len(model_crons), 'threshold_value': MIN_CRONS_PER_MODEL,
                'threshold_label': _('Scheduled actions on one model'),
                'evidence': {'model': model.model, 'crons': listing},
                'references': {'crons': listing},
            })
        return findings
