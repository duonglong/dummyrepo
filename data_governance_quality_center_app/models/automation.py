import calendar
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

from ..services import events

SCHEDULE_FIELDS = {'trigger', 'interval', 'hour', 'weekday', 'monthday', 'user_id', 'active'}
OPERATOR_GROUP = 'data_governance_quality_center_app.group_dgq_operator'


class DataQualityAutomation(models.Model):
    """A scan that starts on a schedule or after a business event, with the team that gets its issues.

    Requirements: FR-DGQ-0063, FR-DGQ-0064, FR-DGQ-0065
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
    last_run = fields.Datetime(readonly=True, copy=False)
    next_run = fields.Datetime(readonly=True, copy=False)
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

    # ---- keeping the schedule and the hooks up to date ----------------------

    @api.model_create_multi
    def create(self, vals_list):
        """Plan the first run of a schedule and start watching the models of an event automation."""
        automations = super().create(vals_list)
        automations._dgq_plan()
        automations._dgq_watch()
        return automations

    def write(self, vals):
        """A new schedule plans its next run again, unless the run is set by hand."""
        result = super().write(vals)
        if SCHEDULE_FIELDS & set(vals) and 'next_run' not in vals:
            self._dgq_plan()
        self._dgq_watch()
        return result

    @api.model
    def _register_hook(self):
        """Remember the models of the active event automations after a registry load."""
        super()._register_hook()
        self.search([('trigger', '=', 'event')])._dgq_watch()

    def _dgq_watch(self):
        """Tell the save hooks which models to look at (all models of event automations)."""
        # sudo: the person saving an automation may not read the model list
        events.watch(self.env, self.sudo().filtered(lambda a: a.trigger == 'event').model_ids.mapped('model'))

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

        Requirements: FR-DGQ-0063
        """
        now = fields.Datetime.now()
        due = self.sudo().search([('trigger', '=', 'schedule'), ('next_run', '<=', now)])  # sudo: the scheduled action is the app
        for automation in due:
            automation.write({'last_run': now, 'next_run': automation._dgq_next_run(now)})
            run, reason = automation._dgq_launch()
            if reason:
                self.env['data.quality.automation.run'].create({
                    'automation_id': automation.id, 'date_start': now, 'date_end': now,
                    'state': 'skipped', 'skip_reason': reason})

    def _dgq_launch_event(self, res_model, res_ids):
        """Start the scan of the waiting records of one model; false when the scope is busy.

        Requirements: FR-DGQ-0064
        """
        self.ensure_one()
        run, reason = self._dgq_launch(res_model, res_ids)
        return not reason

    def _dgq_launch(self, res_model=None, res_ids=None):
        """Open a run and queue its scan; return ``(run, reason)``, the reason when nothing could start.

        A scope that cannot start (an overlapping scan, no rule to scan) leaves nothing behind.

        Requirements: FR-DGQ-0063, FR-DGQ-0064
        """
        self.ensure_one()
        Run = self.env['data.quality.automation.run']
        try:
            with self.env.cr.savepoint():
                run = Run.create({
                    'automation_id': self.id, 'date_start': fields.Datetime.now(), 'state': 'running'})
                scan = self.env['data.quality.scan'].create(self._dgq_scan_values(run, res_model, res_ids))
                scan._dgq_enqueue()
                run.scan_id = scan
        except UserError as error:
            return Run, error.args[0]
        return run, False

    def _dgq_scan_values(self, run, res_model=None, res_ids=None):
        """The scope of the scan an automation starts: its rules, models and companies."""
        self.ensure_one()
        rules, models_ = self.rule_ids, self.model_ids
        if res_model:
            models_ = models_.filtered(lambda model: model.model == res_model)
            rules = rules.filtered(lambda rule: rule.model_name == res_model)
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

    Requirements: FR-DGQ-0063, FR-DGQ-0065
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

    def _dgq_close(self, scan):
        """Record the end of the run's scan; a finished scan hands its new issues to the team.

        Requirements: FR-DGQ-0065
        """
        self.ensure_one()
        vals = {
            'date_end': fields.Datetime.now(),
            'state': 'done' if scan.state == 'done' else 'failed',
            'issues_created': scan.issues_created,
        }
        if scan.state == 'done':
            vals['issues_assigned'] = self._dgq_assign_new_issues(scan)
        self.write(vals)

    def _dgq_assign_new_issues(self, scan):
        """Give the issues the scan created to the team and, when one can take them, to a user.

        The user is the member chosen for the issue's model, else the team leader; an issue stays New
        for the team when no user is chosen or the one chosen cannot take it. Returns how many got a user.

        Requirements: FR-DGQ-0065
        """
        self.ensure_one()
        team = self.automation_id.team_id
        if not team:
            return 0
        # sudo: the app writes the issues its scan created, whoever may read them
        issues = self.env['data.quality.issue'].sudo().search([('first_scan_id', '=', scan.id)])
        issues.write({'team_id': team.id})
        by_user = defaultdict(lambda: issues.browse())
        for issue in issues:
            user = team._dgq_assignee(issue.res_model)
            if team._dgq_can_take(user, issue):
                by_user[user] |= issue
        for user, user_issues in by_user.items():
            user_issues.write({'user_id': user.id})
        return sum(len(user_issues) for user_issues in by_user.values())
