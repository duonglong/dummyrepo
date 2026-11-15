import calendar
import logging
from datetime import timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from odoo import api, fields, models
from odoo.exceptions import AccessError, MissingError, UserError, ValidationError

from ..const import ADMIN_GROUP, AUTOMATION_RETRIES, ESCALATION_DAYS_PARAMETER, ISSUE_OPEN_STATES
from ..services import audit, events
from .scan import ScanBusy

_logger = logging.getLogger(__name__)

SUMMARY_TEMPLATE = 'data_governance_quality_center_app.mail_template_automation_summary'
FAILED_TEMPLATE = 'data_governance_quality_center_app.mail_template_automation_failed'
TODO_ACTIVITY = 'mail.mail_activity_data_todo'

SCHEDULE_FIELDS = {'trigger', 'interval', 'hour', 'weekday', 'monthday', 'user_id', 'active'}
WATCH_FIELDS = {'trigger', 'model_ids', 'active'}


class DataQualityAutomation(models.Model):
    """A scan that starts on a schedule or after a business event, with the team that gets its issues.

    Requirements: FR-DGQ-0063, FR-DGQ-0064, FR-DGQ-0065, FR-DGQ-0066, FR-DGQ-0067, FR-DGQ-0068,
    FR-DGQ-0069
    """
    _name = 'data.quality.automation'
    _description = 'Data Quality Automation'
    _order = 'name'

    name = fields.Char(required=True)
    active = fields.Boolean(default=True)
    trigger = fields.Selection([
        ('schedule', 'Schedule'),
        ('event', 'Business event'),
    ], required=True, default='schedule')
    interval = fields.Selection([
        ('hourly', 'Hourly'),
        ('daily', 'Daily'),
        ('weekly', 'Weekly'),
        ('monthly', 'Monthly'),
    ], default='daily')
    hour = fields.Integer(default=2, help="The hour of the day, from 0 to 23, in the time zone of the responsible user.")
    weekday = fields.Selection([
        ('0', 'Monday'), ('1', 'Tuesday'), ('2', 'Wednesday'), ('3', 'Thursday'),
        ('4', 'Friday'), ('5', 'Saturday'), ('6', 'Sunday'),
    ], default='0')
    monthday = fields.Integer(string='Day of the Month', default=1)
    event = fields.Selection([
        ('create', 'A record is created'),
        ('write', 'A record is changed'),
        ('both', 'A record is created or changed'),
    ], default='create')
    watched_field_ids = fields.Many2many(
        'ir.model.fields', string='Watched Fields',
        help="A change checks the record only when one of these fields is written. Leave empty for any field.")
    model_ids = fields.Many2many('ir.model', string='Models')
    rule_ids = fields.Many2many(
        'data.quality.rule', string='Rules',
        help="The rules to check. Leave empty for every active rule of the chosen models.")
    company_ids = fields.Many2many(
        'res.company', string='Companies', help="Leave empty for all companies.")
    team_id = fields.Many2one('data.quality.team', string='Team', ondelete='set null')
    user_id = fields.Many2one(
        'res.users', string='Responsible', default=lambda self: self.env.user, ondelete='set null')
    send_email = fields.Boolean(
        string='Send E-mails', help="Each person who gets new issues receives one e-mail listing them.")
    create_activity = fields.Boolean(
        string='Create Activities', help="Each new issue gets a to-do for the person it is assigned to.")
    deadline_days = fields.Integer(
        string='Deadline (days)',
        help="New issues are due this many days after they are found. Leave 0 to set no deadline.")
    escalation_days = fields.Integer(
        string='Escalate After (days)',
        help="An open issue of the team that is this many days past its deadline is escalated, once for each deadline. "
             "Leave 0 for the delay of the settings.")
    escalation_user_id = fields.Many2one(
        'res.users', string='Escalate To', ondelete='set null',
        help="Who is told about an overdue issue. Leave empty for the team leader.")
    correction_ids = fields.Many2many(
        'data.quality.correction', 'data_quality_automation_correction_rel', 'automation_id', 'correction_id',
        string='Automatic Corrections', domain="[('auto_approved', '=', True)]",
        help="Corrections approved for automatic use that run on the records a scan finds failing their rule.")
    last_run = fields.Datetime(readonly=True, copy=False)
    next_run = fields.Datetime(readonly=True, copy=False)
    retry_count = fields.Integer(readonly=True, copy=False, help="Retries of the run that failed last.")
    run_ids = fields.One2many('data.quality.automation.run', 'automation_id', string='History')

    @api.constrains('hour', 'monthday')
    def _check_schedule(self):
        """The hour is a clock hour and the day of the month exists in every month.

        Requirements: FR-DGQ-0063
        """
        for automation in self:
            if not 0 <= automation.hour <= 23:
                raise ValidationError(self.env._("The hour must be between 0 and 23."))
            if not 1 <= automation.monthday <= 28:
                raise ValidationError(self.env._("The day of the month must be between 1 and 28."))

    @api.constrains('deadline_days', 'escalation_days')
    def _check_follow_up(self):
        """A deadline is not in the past and the escalation delay is not negative (0 is the settings' delay).

        Requirements: FR-DGQ-0066, FR-DGQ-0067
        """
        for automation in self:
            if automation.deadline_days < 0:
                raise ValidationError(self.env._("The deadline cannot be a negative number of days."))
            if automation.escalation_days < 0:
                raise ValidationError(self.env._("The escalation delay cannot be a negative number of days."))

    @api.constrains('correction_ids')
    def _check_corrections(self):
        """Only a correction a manager approved for automatic use, and linked to a rule, can be attached.

        Requirements: FR-DGQ-0068
        """
        for automation in self:
            for correction in automation.correction_ids:
                if not correction.auto_approved:
                    raise ValidationError(self.env._(
                        "%s is not approved for automatic use: ask a manager to approve it.", correction.name))
                if not correction.rule_id:
                    raise ValidationError(self.env._(
                        "%s is linked to no rule, so an automation would never run it.", correction.name))

    # ---- keeping the schedule and the hooks up to date ----------------------

    @api.model_create_multi
    def create(self, vals_list):
        """Plan the first run of a schedule and start watching the models of an event automation."""
        automations = super().create(vals_list)
        automations._dgq_plan()
        automations._dgq_watch(notify=True)
        return automations

    def write(self, vals):
        """A new schedule plans its next run again, unless the run is set by hand."""
        result = super().write(vals)
        if SCHEDULE_FIELDS & set(vals) and 'next_run' not in vals:
            self._dgq_plan()
        if WATCH_FIELDS & set(vals):
            self._dgq_watch(notify=True)
        if SCHEDULE_FIELDS & set(vals):
            self._dgq_close_unreachable_runs()
        return result

    def _dgq_close_unreachable_runs(self):
        """Fail the runs waiting for a retry of an automation that no longer runs on a schedule.

        Only a schedule resumes a failed scan, so these runs would stay running for ever.

        Requirements: FR-DGQ-0069
        """
        stopped = self.filtered(lambda automation: not automation.active or automation.trigger != 'schedule')
        waiting = self.env['data.quality.automation.run'].sudo().search([  # sudo: the app closes its own runs
            ('automation_id', 'in', stopped.ids), ('state', '=', 'running'), ('scan_id.state', '=', 'failed')])
        for run in waiting:
            run._dgq_fail(self.env._("The automation no longer runs on a schedule."), notify=False)

    @api.model
    def _register_hook(self):
        """Remember the models of the active event automations after a registry load."""
        super()._register_hook()
        self.search([('trigger', '=', 'event')])._dgq_watch()

    def _dgq_watch(self, notify=False):
        """Tell the save hooks which models to look at (all models of event automations).

        With ``notify`` the registry is also flagged as changed, so the other server workers load it
        again and fill their own set of watched models, as the native automations do.

        Requirements: FR-DGQ-0064
        """
        # sudo: the person saving an automation may not read the model list
        events.watch(self.env, self.sudo().filtered(lambda a: a.trigger == 'event').model_ids.mapped('model'))
        if notify:
            self._dgq_notify_workers()

    def _dgq_notify_workers(self):
        """Flag the registry as changed, so every server worker fills its set of watched models again."""
        if self.env.registry.ready and not self.env.context.get('import_file'):
            self.env.transaction.will_change_registry()

    def unlink(self):
        """Other workers stop queueing the saves of a deleted event automation."""
        result = super().unlink()
        self._dgq_notify_workers()
        return result

    def _dgq_plan(self):
        """Set the next due time of the active schedules; clear it for the others."""
        now = fields.Datetime.now()
        for automation in self:
            due = automation.active and automation.trigger == 'schedule'
            automation.next_run = automation._dgq_next_run(now) if due else False

    def _dgq_next_run(self, after):
        """The first due time after a moment (UTC) for the interval, hour, weekday or day of the month.

        Requirements: FR-DGQ-0063
        """
        self.ensure_one()
        try:
            zone = ZoneInfo(self.user_id.tz or 'UTC')
        except ZoneInfoNotFoundError:
            zone = ZoneInfo('UTC')
        local = after.replace(tzinfo=timezone.utc).astimezone(zone)
        if self.interval == 'hourly':
            due = local.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        else:
            due = local.replace(hour=self.hour, minute=0, second=0, microsecond=0)
            if self.interval == 'weekly':
                due += timedelta(days=(int(self.weekday) - due.weekday()) % 7)
                if due <= local:
                    due += timedelta(days=7)
            elif self.interval == 'monthly':
                due = due.replace(day=self.monthday)
                if due <= local:
                    year, month = divmod(due.month, 12)
                    due = due.replace(year=due.year + year, month=month + 1)
                    due = due.replace(day=min(self.monthday, calendar.monthrange(due.year, due.month)[1]))
            elif due <= local:
                due += timedelta(days=1)
        return due.astimezone(timezone.utc).replace(tzinfo=None)

    # ---- running ------------------------------------------------------------

    @api.model
    def _dgq_run_due(self):
        """Start a scan for each active schedule that is due; the next due time is set first.

        A run whose scan failed is resumed instead of starting a new one, while it has retries left.

        Requirements: FR-DGQ-0063, FR-DGQ-0069
        """
        now = fields.Datetime.now()
        due = self.sudo().search([('trigger', '=', 'schedule'), ('next_run', '<=', now)])  # sudo: the scheduled action is the app
        for automation in due:
            automation.write({'last_run': now, 'next_run': automation._dgq_next_run(now)})
            waiting = self.env['data.quality.automation.run'].search([
                ('automation_id', '=', automation.id), ('state', '=', 'running'),
                ('scan_id.state', '=', 'failed')], limit=1)
            if waiting:
                automation._dgq_retry(waiting)
            else:
                stop, _busy = automation._dgq_launch_safely()
                if stop:
                    automation._dgq_record_stop(now, *stop)
            if not self.env['ir.cron']._commit_progress(1):  # a failure of the next automation keeps this one
                break

    def _dgq_retry(self, run):
        """Resume the scan of a failed run from its checkpoint.

        A busy scope is noted on the run and tried again, without using a retry. Any other refusal
        uses one retry, and the run fails when none is left, as a scan that fails again does.

        Requirements: FR-DGQ-0069
        """
        self.ensure_one()
        try:
            with self.env.cr.savepoint():
                run.scan_id.action_resume()
        except ScanBusy as busy:
            run.message = busy.args[0]
        except UserError as refusal:
            run._dgq_attempt_refused(refusal.args[0])
        else:
            run.message = False

    @api.model
    def _dgq_escalate_overdue(self):
        """Escalate the open issues that are past their deadline by the escalation delay.

        Each automation with a team escalates the issues of its team with its own delay and user;
        the other overdue issues use the delay of the settings and their team's leader.

        Requirements: FR-DGQ-0067
        """
        today = fields.Date.today()
        automations = self.sudo().search([('team_id', '!=', False)])  # sudo: the scheduled action is the app
        for automation in automations:
            try:
                with self.env.cr.savepoint():
                    automation._dgq_escalate_team_issues(today)
            except Exception:  # noqa: BLE001 - one team must not stop the escalation of the others
                _logger.exception("Escalation of the team of automation %s failed", automation.id)
            if not self.env['ir.cron']._commit_progress(1):
                return
        self._dgq_escalate_uncovered(automations.team_id, today)

    @api.model
    def _dgq_admins(self):
        """The data quality administrators: told of failed runs and of late issues that have no team."""
        # sudo: the app looks up whom to tell, whatever the runner may read of the users
        return self.env['res.users'].sudo().search([('all_group_ids', 'in', self.env.ref(ADMIN_GROUP).ids)])

    @api.model
    def _dgq_escalation_delay(self):
        """The days of the settings an open issue waits after its deadline (one day until it is set)."""
        # sudo: the delay is read for every user, not only administrators
        return self.env['ir.config_parameter'].sudo().get_int(ESCALATION_DAYS_PARAMETER, 1)

    @api.model
    def _dgq_escalate_uncovered(self, covered_teams, today):
        """Escalate the late issues of teams that no automation covers, and of no team, with the settings' delay.

        A team's leader is told; an issue with no team has no leader, so the administrators are told.

        Requirements: FR-DGQ-0067
        """
        limit = fields.Date.subtract(today, days=self._dgq_escalation_delay())
        late = self.env['data.quality.issue'].sudo().search([  # sudo: the app escalates for the team
            ('team_id', 'not in', covered_teams.ids), ('state', 'in', ISSUE_OPEN_STATES),
            ('deadline', '!=', False), ('deadline', '<=', limit)])
        issues = late.filtered(lambda issue: issue.escalated_deadline != issue.deadline)
        for team, team_issues in issues.grouped('team_id').items():
            try:
                with self.env.cr.savepoint():
                    team_issues._dgq_escalate(team.leader_id if team else self._dgq_admins())
            except Exception:  # noqa: BLE001 - one team must not stop the escalation of the others
                _logger.exception("Escalation of the issues of team %s failed", team.id)
            if not self.env['ir.cron']._commit_progress(1):
                return

    def _dgq_escalate_team_issues(self, today):
        """Escalate the team's late issues not yet escalated for their deadline, and record it in the history.

        Requirements: FR-DGQ-0067, FR-DGQ-0069
        """
        self.ensure_one()
        limit = fields.Date.subtract(today, days=self.escalation_days or self._dgq_escalation_delay())
        late = self.env['data.quality.issue'].sudo().search([  # sudo: the app escalates for the team
            ('team_id', '=', self.team_id.id), ('state', 'in', ISSUE_OPEN_STATES),
            ('deadline', '!=', False), ('deadline', '<=', limit)])
        issues = late.filtered(lambda issue: issue.escalated_deadline != issue.deadline)
        if not issues:
            return
        issues._dgq_escalate(self.escalation_user_id or self.team_id.leader_id)
        now = fields.Datetime.now()
        self.env['data.quality.automation.run'].create({
            'automation_id': self.id, 'date_start': now, 'date_end': now, 'state': 'done',
            'issues_escalated': len(issues),
            'message': self.env._("Escalation of overdue issues")})

    def _dgq_launch_safely(self, res_model=None, res_ids=None):
        """Start a scan; an unexpected error is logged and returned as a failure, never raised.

        Returns ``((state, reason), busy)``: no stop when the scan started; ``busy`` when the scope
        overlaps a running scan and may be tried again.
        """
        self.ensure_one()
        try:
            with self.env.cr.savepoint():
                stop, busy = self._dgq_launch(res_model, res_ids)
        except Exception as error:  # noqa: BLE001 - one automation must not stop the others
            _logger.exception("Automation %s failed to start its scan", self.id)
            return ('failed', str(error)), False
        return stop, busy

    def _dgq_record_stop(self, now, state, reason):
        """Record a run that never started a scan, with the reason."""
        self.ensure_one()
        self.env['data.quality.automation.run'].create({
            'automation_id': self.id, 'date_start': now, 'date_end': now,
            'state': state, 'skip_reason': reason})

    def _dgq_launch_event(self, res_model, res_ids):
        """Start the scan of the waiting records of one model; false only while the scope is busy.

        A model the automation no longer covers, or whose rules it did not choose, has nothing to
        check: the records count as handled and no scan starts. Any other refusal is final: a skipped
        run records the reason and the records count as handled. Only an overlap keeps them waiting.

        Requirements: FR-DGQ-0064
        """
        self.ensure_one()
        if not self._dgq_event_rules(res_model):
            return True
        stop, busy = self._dgq_launch_safely(res_model, res_ids)
        if busy:
            return False
        if stop:
            self._dgq_record_stop(fields.Datetime.now(), *stop)
        return True

    def _dgq_event_rules(self, res_model):
        """The rules an event of the model checks: the chosen ones, or every rule of the model when none is chosen.

        Empty when the model is not one of the automation's, or when rules were chosen and none of
        them is switched on or about this model.

        Requirements: FR-DGQ-0064
        """
        self.ensure_one()
        if res_model not in self.model_ids.mapped('model'):
            return self.env['data.quality.rule']
        chosen = self._dgq_chosen_rules()
        if chosen:
            return chosen.filtered(lambda rule: rule.active and rule.model_name == res_model)
        # sudo: the app looks up the active rules of the model whoever saved the record
        return self.env['data.quality.rule'].sudo().search([('active', '=', True), ('model_name', '=', res_model)])

    def _dgq_chosen_rules(self):
        """The rules named on the automation, switched off ones included (a many2many read hides them)."""
        self.ensure_one()
        return self.with_context(active_test=False).rule_ids

    def _dgq_launch(self, res_model=None, res_ids=None):
        """Open a run and queue its scan; return ``(stop, busy)``.

        ``stop`` is ``(state, reason)`` when nothing could start (an overlapping scan, no rule to
        scan) and leaves nothing behind; ``busy`` is true when only an overlap stopped it.

        Requirements: FR-DGQ-0063, FR-DGQ-0064
        """
        self.ensure_one()
        Run = self.env['data.quality.automation.run']
        try:
            with self.env.cr.savepoint():
                run = Run.create({
                    'automation_id': self.id, 'date_start': fields.Datetime.now(), 'state': 'running',
                    'sends_summary': self.send_email})
                scan = self.env['data.quality.scan'].create(self._dgq_scan_values(run, res_model, res_ids))
                scan._dgq_enqueue()
                run.scan_id = scan
        except (AccessError, MissingError, ValidationError):
            raise
        except ScanBusy as error:
            return ('skipped', error.args[0]), True
        except UserError as error:
            return ('skipped', error.args[0]), False
        return False, False

    def _dgq_scan_values(self, run, res_model=None, res_ids=None):
        """The scope of the scan an automation starts: its rules, models and companies."""
        self.ensure_one()
        chosen = self._dgq_chosen_rules()
        rules, models_ = chosen.filtered('active'), self.model_ids
        if chosen and not rules:
            raise UserError(self.env._("None of the rules chosen for this automation is switched on."))
        if res_model:
            rules = self._dgq_event_rules(res_model)
            models_ = models_.filtered(lambda model: model.model == res_model)
        # sudo: an automation without companies covers every company, whatever the runner may read
        companies = self.company_ids or self.env['res.company'].sudo().search([])
        return {
            'trigger': 'event' if res_model else 'automation',
            'rule_ids': [fields.Command.set(rules.ids)],
            'model_ids': [fields.Command.set(models_.ids)],
            'company_ids': [fields.Command.set(companies.ids)],
            'res_ids': res_ids or False,
            'automation_run_id': run.id,
        }


class DataQualityAutomationRun(models.Model):
    """One start of an automation: the scan it began and what came of it.

    Requirements: FR-DGQ-0063, FR-DGQ-0065, FR-DGQ-0066, FR-DGQ-0068, FR-DGQ-0069
    """
    _name = 'data.quality.automation.run'
    _description = 'Data Quality Automation Run'
    _order = 'id desc'

    automation_id = fields.Many2one('data.quality.automation', required=True, ondelete='cascade', index=True)
    date_start = fields.Datetime(readonly=True)
    date_end = fields.Datetime(readonly=True)
    state = fields.Selection([
        ('running', 'Running'),
        ('done', 'Done'),
        ('failed', 'Failed'),
        ('skipped', 'Skipped'),
    ], default='running', required=True, readonly=True)
    scan_id = fields.Many2one('data.quality.scan', string='Scan', ondelete='set null', readonly=True)
    skip_reason = fields.Char(readonly=True)
    issues_created = fields.Integer(readonly=True)
    issues_assigned = fields.Integer(readonly=True)
    issues_escalated = fields.Integer(readonly=True)
    corrections_applied = fields.Integer(readonly=True)
    retry_count = fields.Integer(readonly=True)
    message = fields.Char(readonly=True)
    sends_summary = fields.Boolean(
        readonly=True, help="Whether the automation sent e-mails when the run started: the summary then "
                            "replaces the assignment notice for the whole run.")
    correction_run_ids = fields.One2many(
        'data.quality.correction.run', 'automation_run_id', string='Correction Runs')

    def _dgq_close(self, scan):
        """Record the end of the run's scan.

        A finished scan goes on with the approved corrections, then the follow-up. A failed scan of a
        schedule is kept for a retry at the next due time, up to the number of retries; after that, and
        for any other end, the run is failed and the administrators are told.

        Requirements: FR-DGQ-0065, FR-DGQ-0068, FR-DGQ-0069
        """
        self.ensure_one()
        if self.state != 'running':
            return
        self.issues_created = scan.issues_created
        automation = self.automation_id
        if scan.state == 'done':
            automation.retry_count = 0
            if not self._dgq_start_corrections(scan):
                self._dgq_finish()
        elif scan.state == 'failed' and automation.trigger == 'schedule' and self._dgq_count_attempt():
            self.message = self.env._("The scan failed: retry %(count)s of %(total)s at the next due time.",
                                      count=self.retry_count, total=AUTOMATION_RETRIES)
        else:
            automation.retry_count = self.retry_count
            if scan.state == 'cancelled':
                self._dgq_fail(self.env._("The scan was cancelled."), notify=False)
            else:
                self._dgq_fail(scan.error_message or self.env._("The scan ended without finishing."))

    def _dgq_count_attempt(self):
        """Use one of the retries of the run; false when none is left.

        Requirements: FR-DGQ-0069
        """
        self.ensure_one()
        if self.retry_count >= AUTOMATION_RETRIES:
            return False
        self.retry_count += 1
        self.automation_id.retry_count = self.retry_count
        return True

    def _dgq_attempt_refused(self, reason):
        """The resume of the run's scan was refused: it uses a retry, and the run fails when none is left.

        Requirements: FR-DGQ-0069
        """
        self.ensure_one()
        if self._dgq_count_attempt():
            self.message = reason
        else:
            self._dgq_fail(reason)

    def _dgq_finish(self):
        """Close a run whose scan and corrections are over, then send its e-mails and activities.

        Requirements: FR-DGQ-0066, FR-DGQ-0069
        """
        self.ensure_one()
        self.write({'state': 'done', 'date_end': fields.Datetime.now()})
        self._dgq_follow_up_safely()

    def _dgq_follow_up_safely(self):
        """Send the run's e-mails and activities; when they fail, the skipped assignment notices go out instead.

        A notice that cannot be sent never undoes the scan: the error is logged and noted on the run.

        Requirements: FR-DGQ-0066
        """
        self.ensure_one()
        try:
            with self.env.cr.savepoint():
                self._dgq_follow_up()
        except Exception:  # noqa: BLE001 - a notice that cannot be sent never undoes the scan
            _logger.exception("Automation run %s could not send its e-mails and activities", self.id)
            self.message = self.env._("The e-mails and activities could not be created. See the server log.")
            self._dgq_send_skipped_notices()

    def _dgq_send_skipped_notices(self):
        """Send the assignment notices the summary was meant to replace, for a summary that could not be sent."""
        if not self.sends_summary:
            return
        try:
            with self.env.cr.savepoint():
                for user, issues in self._dgq_new_assigned_issues().grouped('user_id').items():
                    if user.email:
                        issues._dgq_notify_assignee(user)
        except Exception:  # noqa: BLE001 - logged; the run keeps its result
            _logger.exception("Automation run %s could not send the assignment notices either", self.id)

    def _dgq_fail(self, message, notify=True):
        """Mark the run failed with the reason, follow up the issues it assigned and, unless ``notify`` is off,
        tell the administrators.

        Requirements: FR-DGQ-0066, FR-DGQ-0069
        """
        self.ensure_one()
        self.write({'state': 'failed', 'date_end': fields.Datetime.now(), 'message': message})
        self._dgq_follow_up_safely()
        if not notify:
            return
        admins = self.automation_id._dgq_admins()
        if not admins:
            return
        template = self.env.ref(FAILED_TEMPLATE)
        subject = template._render_field('subject', self.ids)[self.id]
        body = template._render_field('body_html', self.ids)[self.id]
        self.env['mail.thread'].message_notify(partner_ids=admins.partner_id.ids, subject=subject, body=body)

    def _dgq_start_corrections(self, scan):
        """Start the corrections approved for automatic use on the records the scan found failing.

        A correction that needs a person's approval, or whose start is refused, is left out and the
        reason is noted on the run. Returns whether any correction run was started.

        Requirements: FR-DGQ-0068
        """
        self.ensure_one()
        Issue = self.env['data.quality.issue']
        started, left_out = 0, []
        for correction in self.automation_id.correction_ids.filtered(
                lambda c: c.auto_approved and c.rule_id in scan.rule_ids):
            if correction.needs_approval:
                left_out.append(self.env._("%s needs a person's approval.", correction.name))
                continue
            failing = Issue.search([
                ('rule_id', '=', correction.rule_id.id), ('scan_id', '=', scan.id),
                ('state', 'in', ISSUE_OPEN_STATES), ('res_model', '=', correction.model_id.model)])
            if not failing:
                continue
            try:
                with self.env.cr.savepoint():
                    run = self.env['data.quality.correction.run'].create({
                        'correction_id': correction.id, 'kind': 'run',
                        'automation_run_id': self.id, 'res_ids': failing.mapped('res_id')})
                    run.action_start()
                    audit.log(self.env, 'correction_run', correction)
            except UserError as refusal:
                left_out.append("%s: %s" % (correction.name, refusal.args[0]))
                continue
            started += 1
        if left_out:
            self.message = " ".join(left_out)[:255]
        return started

    def _dgq_correction_ended(self):
        """A correction run of this automation run ended: when all did, count them and close the run.

        Requirements: FR-DGQ-0068, FR-DGQ-0069
        """
        self.ensure_one()
        runs = self.correction_run_ids
        if self.state != 'running' or any(run.state in ('draft', 'queued', 'running') for run in runs):
            return
        done = runs.filtered(lambda run: run.state == 'done')
        self.corrections_applied = sum(done.mapped('count_changed'))
        stopped = runs - done
        if stopped:
            self._dgq_fail(self.env._(
                "A correction did not finish: %s.", ", ".join(stopped.correction_id.mapped('name'))))
        else:
            self._dgq_finish()

    def _dgq_follow_up(self):
        """Give each person with new issues of this run one e-mail and a to-do on each issue, as chosen.

        Only the issues the run's scan found and that are still open count: a corrected issue needs
        no follow-up.

        Requirements: FR-DGQ-0066
        """
        self.ensure_one()
        automation = self.automation_id
        if not (self.sends_summary or automation.create_activity):
            return
        issues = self._dgq_new_assigned_issues()
        if automation.create_activity:
            self._dgq_create_activities(issues)
        if self.sends_summary:
            for user, assigned in issues.grouped('user_id').items():
                self._dgq_send_summary(user, assigned)

    def _dgq_new_assigned_issues(self):
        """The issues first found by the run's scan that are still open and have an assignee."""
        self.ensure_one()
        if not self.scan_id:
            return self.env['data.quality.issue']
        return self.env['data.quality.issue'].search([
            ('first_scan_id', '=', self.scan_id.id), ('state', 'in', ISSUE_OPEN_STATES),
            ('user_id', '!=', False)])

    def _dgq_create_activities(self, issues):
        """One to-do per issue for its assignee, due on its deadline (today when it has none)."""
        todo = self.env.ref(TODO_ACTIVITY)
        model_id = self.env['ir.model']._get_id('data.quality.issue')
        today = fields.Date.today()
        self.env['mail.activity'].create([{
            'res_model_id': model_id, 'res_id': issue.id, 'activity_type_id': todo.id,
            'user_id': issue.user_id.id, 'date_deadline': issue.deadline or today,
            'summary': issue.name,
        } for issue in issues])

    def _dgq_send_summary(self, user, issues):
        """One e-mail to a person listing the new issues assigned to them; none without an address."""
        if not user.email:
            return
        template = self.env.ref(SUMMARY_TEMPLATE).with_context(lang=user.lang, dgq_issues=issues)
        subject = template._render_field('subject', self.ids)[self.id]
        body = template._render_field('body_html', self.ids)[self.id]
        self.env['mail.mail'].create({
            'subject': subject, 'body_html': body, 'recipient_ids': [fields.Command.link(user.partner_id.id)],
            'auto_delete': True})

    def _dgq_assign_issues(self, issues):
        """Give the new issues of one scan batch to the team and, when one can take them, to a user.

        The user is the member chosen for the issues' model, else the team leader; the issues stay New
        for the team when no user is chosen or the one chosen cannot take them.

        New issues also get the deadline of the automation.

        Requirements: FR-DGQ-0065, FR-DGQ-0066
        """
        self.ensure_one()
        automation = self.automation_id
        if issues and automation.deadline_days:
            issues.write({'deadline': fields.Date.add(fields.Date.today(), days=automation.deadline_days)})
        team = automation.team_id
        if not team or not issues:
            return
        issues.write({'team_id': team.id})
        user = team._dgq_assignee(issues[0].res_model)
        takers = issues.filtered(lambda issue: team._dgq_can_take(user, issue))
        if takers:
            # the summary e-mail replaces the assignment notice, for a person it can reach
            summary_replaces_notice = self.sends_summary and user.email
            takers.with_context(dgq_no_assign_notice=bool(summary_replaces_notice)).write({'user_id': user.id})
            self.issues_assigned += len(takers)
