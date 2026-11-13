from odoo import api, fields, models
from odoo.exceptions import UserError

from ..services import jobs


class DataQualityJobMixin(models.AbstractModel):
    """Shared state, progress and resume behaviour of every background job.

    A job kind inherits this mixin, sets ``_dgq_cron_xmlid`` to the scheduled action
    that runs it and implements ``_dgq_run_batch``; its scheduled action calls
    ``_dgq_run_next``.

    Requirements: NFR-DGQ-0002, NFR-DGQ-0003, NFR-DGQ-0005
    """
    _name = 'data.quality.job.mixin'
    _description = 'Data quality background job'

    #: external id of the scheduled action that runs this kind of job
    _dgq_cron_xmlid = None

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
        self.write({'state': 'cancelled', 'date_end': fields.Datetime.now()})
        return True

    def _dgq_enqueue(self):
        self.write({
            'state': 'queued',
            'user_id': self.env.user.id,
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
