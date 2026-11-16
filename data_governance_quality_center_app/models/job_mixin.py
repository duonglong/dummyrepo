from odoo import api, fields, models
from odoo.exceptions import UserError

from ..services import jobs


class DataQualityJobMixin(models.AbstractModel):
    """Shared state, progress and resume behaviour of every background job.

    A job kind inherits this mixin, sets ``_dgq_cron_xmlid`` to the scheduled action
    that runs it and implements ``_dgq_run_batch``; its scheduled action calls
    ``_dgq_run_next``.

    A user never writes the state, progress or results of a job: the buttons and the runner do. A kind
    lists its own result fields in ``_dgq_app_fields``. A user writes the scope of a job (the fields a kind
    lists in ``_dgq_scope_fields``) only while the job is draft, or in a state the kind's
    ``_dgq_scope_is_open`` names, because starting it again reads the scope again.

    Requirements: NFR-DGQ-0002, NFR-DGQ-0003, NFR-DGQ-0005, NFR-DGQ-0006, FR-DGQ-0070
    """
    _name = 'data.quality.job.mixin'
    _description = 'Data quality background job'

    #: external id of the scheduled action that runs this kind of job
    _dgq_cron_xmlid = None
    #: the fields only the app writes; a kind adds its own results to these
    _dgq_app_fields = frozenset({
        'state', 'user_id', 'date_queued', 'date_start', 'date_end', 'heartbeat', 'progress',
        'total_count', 'done_count', 'error_message',
    })

    #: the fields that say what a job works on; a kind lists its own
    _dgq_scope_fields = frozenset()

    state = fields.Selection([
        ('draft', 'Draft'),
        ('queued', 'Queued'),
        ('running', 'Running'),
        ('done', 'Done'),
        ('failed', 'Failed'),
        ('cancelled', 'Cancelled'),
    ], default='draft', required=True, index=True, copy=False)
    user_id = fields.Many2one('res.users', string="Requested by", ondelete='set null', copy=False)
    date_queued = fields.Datetime(copy=False)
    date_start = fields.Datetime(copy=False)
    date_end = fields.Datetime(copy=False)
    heartbeat = fields.Datetime(copy=False)
    progress = fields.Float(compute='_compute_progress', store=True)
    total_count = fields.Integer(copy=False)
    done_count = fields.Integer(copy=False)
    error_message = fields.Text(copy=False)

    @api.model_create_multi
    def create(self, vals_list):
        """A user creates a job with its own settings; a field of the app is accepted only at its default.

        A form sends the defaults of every field of a new record, so a value equal to the default is
        no hand setting. Defaults passed in the context are left out for these fields: the ORM applies
        them after the check of the values.

        Requirements: NFR-DGQ-0006
        """
        if not self.env.su:
            self = self.with_context({
                key: value for key, value in self.env.context.items()
                if key.removeprefix('default_') not in self._dgq_app_fields})
            names = {name for vals in vals_list for name in vals if name in self._dgq_app_fields}
            defaults = self.default_get(sorted(names))
            for vals in vals_list:
                self._dgq_check_app_values({
                    name for name in names.intersection(vals) if vals[name] != defaults.get(name, False)})
        return super().create(vals_list)

    def write(self, vals):
        """A user changes the settings of a job, never its state, progress or results, and its scope only
        while the job has not run.

        Requirements: NFR-DGQ-0006, FR-DGQ-0070
        """
        if not self.env.su:
            self._dgq_check_app_values(set(vals))
            if self._dgq_scope_fields.intersection(vals) and self.filtered(lambda job: not job._dgq_scope_is_open()):
                raise UserError(self.env._(
                    "The scope of a job can only be changed before it is started. Create a new one instead."))
        return super().write(vals)

    def _dgq_scope_is_open(self):
        """Whether the scope of this job may still be written: starting it reads the scope again."""
        self.ensure_one()
        return self.state == 'draft'

    @api.model
    def _dgq_check_app_values(self, names):
        """Refuse the fields only the app writes."""
        forbidden = names & self._dgq_app_fields
        if forbidden:
            raise UserError(self.env._(
                "These fields are set by the app, not by hand: %s. "
                "A job moves on only through Start, Resume and Cancel.",
                ", ".join(sorted(self._fields[name]._description_string(self.env) for name in forbidden))))

    def _dgq_write_as_app(self, vals):
        """Write the fields of a job for a user who may change it."""
        self.check_access('write')
        return self.sudo().write(vals)  # sudo: the app writes the state and results, after the access check

    @api.depends('total_count', 'done_count', 'state')
    def _compute_progress(self):
        """Percentage of the work done; a finished job is complete."""
        for job in self:
            if job.state == 'done':
                job.progress = 100.0
            elif job.total_count:
                job.progress = min(100.0, 100.0 * job.done_count / job.total_count)
            else:
                job.progress = 0.0

    def action_start(self):
        """Queue the job and trigger the scheduled action: the request returns at once."""
        for job in self:
            if job.state not in ('draft', 'failed', 'cancelled'):
                raise UserError(self.env._("Only a draft, failed or cancelled job can be started."))
        return self._dgq_enqueue()

    def action_resume(self):
        """Queue a failed job again; it continues after its checkpoint."""
        for job in self:
            if job.state != 'failed':
                raise UserError(self.env._("Only a failed job can be resumed."))
        return self._dgq_enqueue()

    def action_cancel(self):
        """Cancel a job that is not finished."""
        for job in self:
            if job.state not in ('draft', 'queued', 'running'):
                raise UserError(self.env._("A finished job cannot be cancelled."))
        self._dgq_write_as_app({'state': 'cancelled', 'date_end': fields.Datetime.now()})
        return True

    def _dgq_requester(self):
        """The user a job is queued for: the one who asks; a kind started for someone else says who."""
        self.ensure_one()
        return self.env.user

    def _dgq_enqueue(self):
        for requester, jobs_ in self.grouped(lambda job: job._dgq_requester()).items():
            jobs_._dgq_write_as_app({
                'state': 'queued',
                'user_id': requester.id,
                'date_queued': fields.Datetime.now(),
                'error_message': False,
            })
        self._dgq_trigger_cron()
        return True

    def _dgq_trigger_cron(self):
        """Wake the scheduled action of this job kind, if the kind declares one."""
        cron = self._dgq_cron_xmlid and self.env.ref(self._dgq_cron_xmlid, raise_if_not_found=False)
        if cron:
            cron.sudo()._trigger()  # a user may not read scheduled actions

    @api.model
    def _dgq_run_next(self):
        """Entry point of the scheduled action of a job kind."""
        return jobs.run_next_job(self.env, self._name)

    def _dgq_run_batch(self):
        """Process one batch; return ``(processed, remaining)`` counts.

        Called by the runner with the job as ``self``; the job kind keeps its own
        checkpoint so the next call continues after it. A kind with no batch work
        has nothing to process and its job completes at once.
        """
        return 0, 0

    def _dgq_record_batch(self, processed, remaining):
        """Store the progress of a batch and refresh the heartbeat."""
        vals = {
            'done_count': self.done_count + processed,
            'total_count': max(self.total_count, self.done_count + processed + remaining),
            'heartbeat': fields.Datetime.now(),
        }
        if not remaining:
            vals.update(state='done', date_end=fields.Datetime.now())
        self.write(vals)
