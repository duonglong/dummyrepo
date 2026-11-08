import logging
import time
from datetime import timedelta, timezone

from psycopg2.errors import QueryCanceled

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.modules import module as odoo_module
from odoo.tools.binary import BinaryBytes

from ..checks import base as checks_base

_logger = logging.getLogger(__name__)

STALE_RUN_AFTER = timedelta(hours=1)
MILLISECONDS_PER_SECOND = 1000


class PerfAuditRun(models.Model):
    """One audit of the database: its timing, state and the results of every check.

    Requirements: FR-OPA-0001, FR-OPA-0002, FR-OPA-0003, FR-OPA-0034, BR-OPA-0002
    """

    _name = 'perf.audit.run'
    _description = 'Performance audit run'
    _order = 'start_date desc, id desc'

    name = fields.Char(compute='_compute_name', store=True)
    state = fields.Selection(
        [('running', 'Running'), ('done', 'Done'), ('failed', 'Failed')],
        required=True, default='running', index=True)
    trigger = fields.Selection(
        [('manual', 'Manual'), ('scheduled', 'Scheduled')], required=True, default='manual')
    user_id = fields.Many2one('res.users', ondelete='set null')
    failure_reason = fields.Char()
    start_date = fields.Datetime(required=True, default=fields.Datetime.now, index=True)
    end_date = fields.Datetime()
    stats_reset_date = fields.Datetime()
    stats_reset_source = fields.Selection([('reset', 'Statistics reset'), ('server_start', 'Server start')])
    weak_evidence = fields.Boolean()
    thresholds_used = fields.Json()
    previous_run_id = fields.Many2one('perf.audit.run', ondelete='set null')
    check_result_ids = fields.One2many('perf.audit.check.result', 'run_id')
    finding_ids = fields.One2many('perf.audit.finding', 'run_id')
    finding_count = fields.Integer()
    new_count = fields.Integer()
    fixed_count = fields.Integer()
    regressed_count = fields.Integer()
    is_latest = fields.Boolean(compute='_compute_is_latest')
    fix_script = fields.Binary(compute='_compute_fix_script', attachment=False)

    @api.depends('start_date')
    def _compute_name(self):
        for run in self:
            run.name = _('Audit %s', fields.Datetime.to_string(run.start_date)[:16]) if run.start_date else _('Audit')

    def _compute_is_latest(self):
        latest = self.search([('state', '=', 'done')], order='start_date desc, id desc', limit=1)
        for run in self:
            run.is_latest = run == latest

    @api.depends('finding_ids')
    def _compute_fix_script(self):
        """Build the SQL file of the run: a comment per finding and its statement, none of them run.

        Requirements: FR-OPA-0028
        """
        for run in self:
            lines = ['-- Fix script of %s' % run.name]
            for finding in run.finding_ids.filtered(lambda f: f.comparison_state != 'fixed'):
                lines.append('-- [%s] %s' % (finding.code, ' '.join(finding.message.split())))
                lines.append(self._evidence_comment(finding))
                if finding.code_suggestion:
                    lines.extend('-- Suggestion: ' + line for line in finding.code_suggestion.splitlines())
                if finding.fix_statement:
                    lines.append(finding.fix_statement + ';')
            run.fix_script = BinaryBytes(
                ('\n'.join(lines) + '\n').encode(), filename='perf_audit_fix_script.sql')

    @staticmethod
    def _evidence_comment(finding):
        """One comment line with the evidence of a finding."""
        parts = [
            'table %s' % finding.table_name if finding.table_name else '',
            'about %s rows (estimate)' % finding.row_estimate if finding.row_estimate else '',
            '%s: found %s, threshold %s' % (
                finding.threshold_label, finding.found_value, finding.threshold_value)
            if finding.threshold_label else '',
        ]
        evidence = ', '.join(part for part in parts if part) or 'none'
        return '-- Evidence: ' + ' '.join(evidence.split())

    # -- start ------------------------------------------------------------

    @api.model
    def action_start_audit(self):
        """Start an audit run by hand, refusing while another one runs.

        A run left running for more than an hour is closed as failed first.
        The request commits the run in state running, then a background action executes it.

        Requirements: FR-OPA-0001, FR-OPA-0034
        """
        self._close_stale_runs()
        running = self.search([('state', '=', 'running')], limit=1)
        if running:
            raise UserError(_('The audit run "%s" is already in progress.', running.name))
        run = self._start_run('manual')
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': run.id,
            'view_mode': 'form',
        }

    @api.model
    def _cron_start_scheduled_audit(self):
        """Start a run for the scheduled audit; skipped silently while another one runs.

        The run starts like one started by hand: this action ends and commits it in state running,
        then the background action executes it.

        Requirements: FR-OPA-0032, FR-OPA-0034
        """
        self._close_stale_runs()
        if self.search_count([('state', '=', 'running')]):
            _logger.info('Scheduled audit skipped: a run is already in progress')
            return
        self._start_run('scheduled')

    @api.model
    def _start_run(self, trigger):
        """Create a running run started by the current user with `trigger` and have it executed.

        A run is executed at once in a test, else by the background action once the run is committed.

        Requirements: FR-OPA-0001, FR-OPA-0032
        """
        run = self.create({'user_id': self.env.user.id, 'trigger': trigger})
        if odoo_module.current_test:
            run._execute()
            return run
        # The trigger wakes the background action after the caller's transaction commits the run.
        cron = self.env.ref('perf_audit.ir_cron_execute_audit').sudo()  # an administrator may not own the cron
        cron._trigger()
        return run

    @api.model
    def _close_stale_runs(self):
        """Close as failed the runs still running after the stale limit.

        Requirements: FR-OPA-0034
        """
        stale = self.search([
            ('state', '=', 'running'),
            ('start_date', '<', fields.Datetime.now() - STALE_RUN_AFTER),
        ])
        stale.write({
            'state': 'failed',
            'end_date': fields.Datetime.now(),
            'failure_reason': _('Closed as failed: it was still running after one hour.'),
        })

    @api.model
    def _execute_pending_runs(self):
        """Execute the runs started and not yet executed; called by the background action."""
        for run in self.search([('state', '=', 'running'), ('check_result_ids', '=', False)], order='id'):
            try:
                with self.env.cr.savepoint():
                    run._execute()
            except Exception as error:  # noqa: BLE001 - an unexpected error closes the run as failed
                _logger.exception('Audit run %s failed', run.name)
                run.write({
                    'state': 'failed', 'end_date': fields.Datetime.now(),
                    'failure_reason': str(error) or type(error).__name__,
                })

    # -- execution --------------------------------------------------------

    @api.model
    def _check_registry(self):
        """The checks available to a run by code; the seam tests replace."""
        return checks_base.registry()

    def _global_thresholds(self):
        """Values of the thresholds that belong to no check, by key."""
        # the thresholds are read for the run itself, whoever executes it
        globals_ = self.env['perf.audit.threshold'].sudo().search([('check_id', '=', False)])
        return {threshold.key: threshold.value for threshold in globals_}

    def _execute(self):
        """Run every check in sequence order and close the run.

        Requirements: FR-OPA-0001, FR-OPA-0003, BR-OPA-0002
        """
        self.ensure_one()
        global_thresholds = self._global_thresholds()
        self.thresholds_used = global_thresholds
        self._record_stats_reset(global_thresholds['weak_evidence'])
        registry = self._check_registry()
        for check in self.env['perf.audit.check'].sudo().search([]):  # the background action runs without the user
            self._execute_check(check, registry.get(check.code), global_thresholds)
        self.env.flush_all()
        self._compare_with_earlier_runs()
        self.write({'state': 'done', 'end_date': fields.Datetime.now()})

    def _compare_with_earlier_runs(self):
        """Mark each finding new, open or regressed, list the gone ones as fixed and store the counts.

        A finding is matched by its check and identity key (BR-OPA-0003). Open means it is also in the
        previous done run (not marked fixed there); regressed means it is only in an earlier done run.
        A finding of the previous run whose check did not finish in this run is not listed as fixed.
        Calling it twice gives the same result: the fixed rows of an earlier call are rebuilt.

        Requirements: FR-OPA-0030, BR-OPA-0003
        """
        self.ensure_one()
        Finding = self.env['perf.audit.finding']
        self.finding_ids.filtered(lambda f: f.comparison_state == 'fixed').unlink()
        earlier_runs = self.search([
            ('state', '=', 'done'), ('id', '!=', self.id), ('start_date', '<=', self.start_date),
        ], order='start_date desc, id desc')
        previous = earlier_runs[:1]
        self.previous_run_id = previous
        in_previous = previous.finding_ids.filtered(lambda f: f.comparison_state != 'fixed')
        previous_marks = {(f.check_id.id, f.identity_key): f for f in in_previous}
        seen_before = {
            (f.check_id.id, f.identity_key)
            for f in earlier_runs[1:].finding_ids if f.comparison_state != 'fixed'}
        for finding in self.finding_ids:
            mark = (finding.check_id.id, finding.identity_key)
            if mark in previous_marks:
                finding.comparison_state = 'open'
            elif mark in seen_before:
                finding.comparison_state = 'regressed'
            else:
                finding.comparison_state = 'new'
        checked = self.check_result_ids.filtered(lambda r: r.state == 'done').check_id
        current = {(f.check_id.id, f.identity_key) for f in self.finding_ids}
        gone = in_previous.filtered(lambda f: f.check_id in checked and (f.check_id.id, f.identity_key) not in current)
        for finding in gone:
            finding.copy({'run_id': self.id, 'comparison_state': 'fixed'})
        self._store_comparison_counts()

    def _store_comparison_counts(self):
        """Store the finding, new, fixed and regressed counts of the run.

        Requirements: FR-OPA-0030
        """
        states = self.finding_ids.mapped('comparison_state')
        self.write({
            'finding_count': len(states) - states.count('fixed'),
            'new_count': states.count('new'),
            'fixed_count': states.count('fixed'),
            'regressed_count': states.count('regressed'),
        })

    def _record_stats_reset(self, weak_evidence_days):
        """Store when the statistics were last reset and whether that makes the evidence weak.

        Requirements: FR-OPA-0004
        """
        reset_date, source = self.env['perf.audit.probe'].stats_reset()
        reset_date = reset_date.astimezone(timezone.utc).replace(tzinfo=None)
        self.write({
            'stats_reset_date': reset_date,
            'stats_reset_source': source,
            'weak_evidence': reset_date > fields.Datetime.now() - timedelta(days=weak_evidence_days),
        })

    def _execute_check(self, check, check_impl, global_thresholds):
        """Run one check and store its result line; never raises for a failing check.

        Requirements: FR-OPA-0001, FR-OPA-0002, FR-OPA-0003
        """
        thresholds = dict(global_thresholds)
        thresholds.update({threshold.key: threshold.value for threshold in check.threshold_ids})
        result = {
            'run_id': self.id, 'check_id': check.id, 'thresholds_used': thresholds,
            'state': 'skipped', 'finding_count': 0, 'duration': 0.0,
        }
        ctx = checks_base.AuditContext(
            env=self.env, probe=self.env['perf.audit.probe'], thresholds=thresholds, run=self)
        if not check.active_check:
            result['reason'] = _('Switched off')
        elif check_impl is None:
            result['reason'] = _('No implementation of this check is installed')
        elif reason := check_impl.needs(ctx):
            result['reason'] = reason
        else:
            result.update(self._run_check_guarded(check, check_impl, ctx))
        self.env['perf.audit.check.result'].create(result)

    def _run_check_guarded(self, check, check_impl, ctx):
        """Run a check in a savepoint under the query time limit; return its result values.

        Requirements: FR-OPA-0002
        """
        probe = ctx.probe
        milliseconds = ctx.thresholds['query_time_limit'] * MILLISECONDS_PER_SECOND
        started = time.monotonic()
        try:
            with self.env.cr.savepoint():
                previous_limit = probe.set_statement_timeout(milliseconds)
                findings = check_impl.run(ctx)
                created = self.env['perf.audit.finding'].create(
                    [self._finding_values(check, finding, rank)
                     for rank, finding in enumerate(findings, 1)])
                probe.restore_statement_timeout(previous_limit)
        except QueryCanceled:
            outcome = {'state': 'failed', 'reason': _('Time limit reached')}
        except Exception as error:  # noqa: BLE001 - one failing check must not stop the others
            _logger.warning('Audit check %s failed', check.code, exc_info=True)
            outcome = {'state': 'failed', 'reason': str(error) or type(error).__name__}
        else:
            outcome = {'state': 'done', 'finding_count': len(created), **ctx.outcome}
        outcome['duration'] = time.monotonic() - started
        return outcome

    def _finding_values(self, check, finding, rank):
        """Values of one finding of `check`, with its rank and the severity BR-OPA-0005 gives when it is not set.

        Requirements: FR-OPA-0005, FR-OPA-0012, BR-OPA-0002
        """
        values = dict(finding, run_id=self.id, check_id=check.id, check_rank=rank)
        if not values.get('severity'):
            values['severity'] = checks_base.severity_for(check.code, values.get('kind'))
        return values
