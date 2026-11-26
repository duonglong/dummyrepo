import logging

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models, modules
from odoo.exceptions import UserError, ValidationError
from odoo.tools import SQL
from odoo.tools.misc import format_date

from ..services import profiler
from ..services.const import RUN_LOCK_KEY
from .snapshot import DpaSnapshotFrozen

_logger = logging.getLogger(__name__)

OWN_MODEL_PREFIX = 'dpa.'


class DpaProfile(models.Model):
    _name = 'dpa.profile'
    _description = 'Data profile'
    _order = 'name, id'

    name = fields.Char(required=True)
    active = fields.Boolean(default=True)
    model_id = fields.Many2one('ir.model', string="Model", required=True, ondelete='cascade', index=True)
    model_name = fields.Char(related='model_id.model', store=True, index=True)
    field_ids = fields.Many2many(
        'ir.model.fields', string="Fields",
        help="Fields to profile; leave empty to profile every field that can be profiled.")
    allowed_model_domain = fields.Json(compute='_compute_allowed_domains')
    allowed_field_domain = fields.Json(compute='_compute_allowed_domains')
    empty_threshold = fields.Float(
        string="Empty-value threshold (%)", required=True, default=20.0,
        help="A field is flagged when its share of empty values is above this percentage.")
    outdated_days = fields.Integer(
        string="Outdated period (days)", required=True, default=365,
        help="A record is outdated when it was not updated for more than this many days.")

    schedule_active = fields.Boolean(string="Schedule")
    schedule_interval_number = fields.Integer(string="Every", default=1)
    schedule_interval_type = fields.Selection(
        [('days', "Days"), ('weeks', "Weeks"), ('months', "Months")],
        string="Interval", default='weeks', required=True)
    schedule_next_run = fields.Datetime(string="Next run")
    schedule_owner_id = fields.Many2one(
        'res.users', string="Schedule owner", readonly=True, ondelete='set null',
        help="The scheduled run counts what this person may read.")
    schedule_company_ids = fields.Many2many(
        'res.company', string="Companies counted", readonly=True,
        help="The companies the owner had selected when the schedule was turned on.")
    schedule_stop_date = fields.Datetime(readonly=True)
    schedule_failure_date = fields.Datetime(readonly=True)
    schedule_failure_message = fields.Char(readonly=True)
    schedule_notice = fields.Char(compute='_compute_schedule_notice')

    snapshot_ids = fields.One2many(
        'dpa.snapshot', 'profile_id', string="Snapshots", domain=[('is_saved', '=', True)])
    snapshot_count = fields.Integer(compute='_compute_snapshot_count')

    @api.depends('snapshot_ids')
    def _compute_snapshot_count(self):
        """Count the saved snapshots of each profile.

        Requirements: FR-DPA-0031
        """
        for profile in self:
            profile.snapshot_count = len(profile.snapshot_ids)

    def action_open_snapshots(self):
        """Open the saved snapshots of this profile.

        Requirements: FR-DPA-0018
        """
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id('data_profiling_analysis_app.dpa_snapshot_action')
        return {**action, 'context': {'search_default_profile_id': self.id}}

    @api.ondelete(at_uninstall=False)
    def _unlink_except_snapshots(self):
        """Keep a profile that has snapshots; drop only its unsaved report.

        Requirements: FR-DPA-0031
        """
        if self.snapshot_ids:
            raise UserError(self.env._("This profile has snapshots. Archive it, or delete its snapshots first."))
        self.env['dpa.snapshot'].search([('profile_id', 'in', self.ids), ('is_saved', '=', False)]).unlink()

    def _model_is_profilable(self, model_name):
        """Whether the current user may profile the model: a real table, readable, not an app model."""
        registry_model = self.env.registry.get(model_name)
        return (
            registry_model is not None
            and registry_model._auto
            and not registry_model._abstract
            and not registry_model._transient
            and not model_name.startswith(OWN_MODEL_PREFIX)
            and self.env[model_name].has_access('read')
        )

    @property
    def allowed_model_ids(self):
        """Models the current user may profile.

        Requirements: FR-DPA-0001
        """
        return self.env['ir.model'].search([]).filtered(lambda m: self._model_is_profilable(m.model))

    @property
    def allowed_field_ids(self):
        """Fields of the chosen model that can be profiled.

        Requirements: BR-DPA-0002
        """
        self.ensure_one()
        if not self.model_id or self.model_name not in self.env:
            return self.env['ir.model.fields']
        return self.env['ir.model.fields'].search([
            ('model_id', '=', self.model_id.id),
            ('name', 'in', self._profilable_field_names()),
        ])

    @api.depends('model_id')
    @api.depends_context('uid')
    def _compute_allowed_domains(self):
        """Domains of the model and field pickers, cut to what the user may profile.

        Requirements: FR-DPA-0001, BR-DPA-0002
        """
        models_domain = [('id', 'in', self.allowed_model_ids.ids)]
        for profile in self:
            profile.allowed_model_domain = models_domain
            profile.allowed_field_domain = [('id', 'in', profile.allowed_field_ids.ids)]

    def _profilable_field_names(self):
        """Names of the stored, readable fields of the model that can be profiled.

        Requirements: BR-DPA-0002
        """
        self.ensure_one()
        return profiler.profilable_fields(self.env, self.model_name)

    def action_run(self):
        """Run the profile as the current user and open the new report.

        Requirements: FR-DPA-0003, FR-DPA-0029, FR-DPA-0033, NFR-REL-0001
        """
        self.ensure_one()
        self._take_run_lock()
        run_date = fields.Datetime.now()
        result = profiler.profile(
            self.env, self.model_name, self.field_ids.mapped('name'), self.outdated_days, run_date)
        report = self.env['dpa.snapshot']._store_report(self, result, run_date)
        return {
            'type': 'ir.actions.act_window',
            'name': report.name,
            'res_model': 'dpa.snapshot',
            'res_id': report.id,
            'view_mode': 'form',
        }

    def _try_run_lock(self):
        """Take the run lock of this profile until the transaction ends; False when another run holds it.

        Requirements: FR-DPA-0029
        """
        self.env.cr.execute(SQL("SELECT pg_try_advisory_xact_lock(%s, %s)", RUN_LOCK_KEY, self.id))
        return self.env.cr.fetchone()[0]

    def _take_run_lock(self):
        """Hold the run lock of this profile until the transaction ends, or refuse the run.

        Requirements: FR-DPA-0029
        """
        if not self._try_run_lock():
            raise UserError(self.env._("This profile is already running. Try again when it has finished."))

    @api.onchange('model_id')
    def _onchange_model_id(self):
        self.field_ids = False

    @api.model_create_multi
    def create(self, vals_list):
        """Create profiles, refusing at once a model the user cannot profile.

        Requirements: FR-DPA-0001
        """
        profiles = super().create(vals_list)
        profiles._check_model_readable()
        profiles.filtered('schedule_active')._start_schedule()
        return profiles

    def write(self, vals):
        """Archiving stops the schedule; turning it on makes the current user its owner.

        Requirements: FR-DPA-0019, FR-DPA-0031
        """
        if 'active' in vals and not vals['active']:
            vals = {**vals, 'schedule_active': False}
        turning_on = self.filtered(lambda profile: not profile.schedule_active) if vals.get('schedule_active') \
            else self.browse()
        result = super().write(vals)
        if turning_on:
            turning_on._start_schedule()
        return result

    def _start_schedule(self):
        """Make the current user the owner of the schedule, with the companies selected now.

        Requirements: FR-DPA-0019
        """
        self.write({
            'schedule_owner_id': self.env.user.id,
            'schedule_company_ids': [fields.Command.set(self.env.companies.ids)],
            'schedule_stop_date': False,
            'schedule_failure_date': False,
            'schedule_failure_message': False,
        })

    @api.constrains('schedule_interval_number')
    def _check_schedule_interval(self):
        """Refuse a schedule interval below one.

        Requirements: FR-DPA-0020
        """
        if any(profile.schedule_interval_number < 1 for profile in self):
            raise ValidationError(self.env._("The schedule interval must be at least 1."))

    @api.constrains('schedule_active', 'schedule_next_run')
    def _check_schedule_next_run(self):
        """Refuse a schedule that has no next run date.

        Requirements: FR-DPA-0019
        """
        if any(profile.schedule_active and not profile.schedule_next_run for profile in self):
            raise ValidationError(self.env._("Set the next run date of the schedule."))

    @api.depends('schedule_active', 'schedule_stop_date', 'schedule_failure_date', 'schedule_failure_message')
    def _compute_schedule_notice(self):
        """Say why the schedule stopped, or that its last run failed.

        Requirements: FR-DPA-0022, NFR-REL-0001
        """
        for profile in self:
            if profile.schedule_stop_date and not profile.schedule_active:
                profile.schedule_notice = self.env._(
                    "The schedule was stopped on %s because its owner can no longer run profiling.",
                    profile._local_date(profile.schedule_stop_date))
            elif profile.schedule_failure_date:
                profile.schedule_notice = self.env._(
                    "The scheduled run of %(date)s failed: %(message)s. It is tried again at the next check.",
                    date=profile._local_date(profile.schedule_failure_date),
                    message=profile.schedule_failure_message)
            else:
                profile.schedule_notice = False

    def _local_date(self, moment):
        """The date of a moment in the reader's time zone, formatted for the reader's language."""
        return format_date(self.env, fields.Datetime.context_timestamp(self, moment).date())

    @api.model
    def _cron_take_snapshots(self):
        """Take a snapshot of every scheduled profile that is due, one profile at a time.

        Requirements: FR-DPA-0021, FR-DPA-0022, FR-DPA-0029, NFR-REL-0001
        """
        now = fields.Datetime.now()
        # sudo: the scheduled action reads the profiles of every analyst and writes their schedule state;
        # the profiling itself runs as the owner of each schedule.
        due = self.sudo().search(
            [('schedule_active', '=', True), ('schedule_next_run', '<=', now)], order='schedule_next_run, id')
        for profile in due:
            profile._run_due_schedule(now)
            if not modules.module.current_test:  # a test may not commit its transaction
                self.env['ir.cron']._commit_progress(1)

    def _run_due_schedule(self, now):
        """Stop the schedule of an owner without access, or take the scheduled snapshot as the owner.

        Requirements: FR-DPA-0021, FR-DPA-0022, FR-DPA-0029, NFR-REL-0001
        """
        self.ensure_one()
        owner = self.schedule_owner_id
        group = self.env.ref('data_profiling_analysis_app.group_data_profiling')
        if not owner.active or group not in owner.all_group_ids:
            self.write({'schedule_active': False, 'schedule_stop_date': now})
            return
        if not self._try_run_lock():
            return
        try:
            with self.env.cr.savepoint():
                self._take_scheduled_snapshot(owner, now)
        except Exception as error:  # noqa: BLE001 - one failing profile must not stop the others
            _logger.exception(
                "Scheduled profiling of profile %s (model %s, owner %s) failed", self.id, self.model_name, owner.id)
            self.write({'schedule_failure_date': now, 'schedule_failure_message': str(error)})

    def _take_scheduled_snapshot(self, owner, now):
        """Run the profile as its owner, keep the result as a scheduled snapshot and move the next run.

        Requirements: FR-DPA-0021
        """
        companies = self.schedule_company_ids & owner.company_ids or owner.company_id
        as_owner = self.with_user(owner).with_context(allowed_company_ids=companies.ids)
        result = profiler.profile(
            as_owner.env, self.model_name, self.field_ids.mapped('name'), self.outdated_days, now)
        report = as_owner.env['dpa.snapshot']._store_report(as_owner, result, now)
        super(DpaSnapshotFrozen, report).write({'is_saved': True, 'origin': 'scheduled'})
        self.write({
            'schedule_next_run': self._next_run_after(now),
            'schedule_failure_date': False,
            'schedule_failure_message': False,
        })

    def _next_run_after(self, now):
        """The first run date after now, a whole number of intervals after the current one.

        Requirements: FR-DPA-0021
        """
        step = 1
        while (candidate := self.schedule_next_run + relativedelta(
                **{self.schedule_interval_type: step * self.schedule_interval_number})) <= now:
            step += 1
        return candidate

    @api.constrains('model_id')
    def _check_model_readable(self):
        """Refuse a model the user cannot profile.

        Requirements: FR-DPA-0001
        """
        for profile in self:
            if not self._model_is_profilable(profile.model_id.model):
                raise ValidationError(self.env._("You are not allowed to read the records of this model."))

    @api.constrains('field_ids', 'model_id')
    def _check_fields_profilable(self):
        """Refuse a field that cannot be profiled on the chosen model.

        Requirements: BR-DPA-0002
        """
        for profile in self.filtered('field_ids'):
            if profile.field_ids - profile.allowed_field_ids:
                raise ValidationError(self.env._("This field cannot be profiled."))

    @api.constrains('empty_threshold')
    def _check_empty_threshold(self):
        """Refuse a threshold outside 0 to 100.

        Requirements: FR-DPA-0006
        """
        if any(not 0 <= profile.empty_threshold <= 100 for profile in self):
            raise ValidationError(self.env._("The empty-value threshold must be between 0 and 100."))

    @api.constrains('outdated_days')
    def _check_outdated_days(self):
        """Refuse an outdated period below one day.

        Requirements: FR-DPA-0012
        """
        if any(profile.outdated_days < 1 for profile in self):
            raise ValidationError(self.env._("The outdated period must be at least 1 day."))
