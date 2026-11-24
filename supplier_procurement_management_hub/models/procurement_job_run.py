from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError


class ProcurementJobRun(models.Model):
    """One run of a scheduled job: when it ran, what it did and what failed.

    Requirements: FR-SPM-0096, FR-SPM-0097
    Rows are written by the job runner only (elevated, after its own checks);
    no role has create or write access.
    """
    _name = 'procurement.job.run'
    _description = 'Scheduled Job Run'
    _order = 'id desc'

    job_id = fields.Many2one('procurement.job', required=True, ondelete='cascade', index=True, readonly=True)
    state = fields.Selection([
        ('running', 'Running'),
        ('done', 'Done'),
        ('failed', 'Failed'),
        ('skipped', 'Skipped'),
    ], required=True, default='running', readonly=True, index=True)
    trigger = fields.Selection([
        ('scheduled', 'Scheduled'),
        ('manual', 'Run now'),
    ], required=True, default='scheduled', readonly=True)
    date_start = fields.Datetime(required=True, readonly=True)
    date_end = fields.Datetime(readonly=True)
    processed_count = fields.Integer(readonly=True)
    sent_count = fields.Integer(string="Notifications sent", readonly=True)
    errors = fields.Json(readonly=True, help="One entry per record that failed: model, id, name and message.")
    error_count = fields.Integer(compute='_compute_error_count', store=True)
    error_summary = fields.Text(compute='_compute_error_summary')

    @api.depends('job_id', 'date_start')
    def _compute_display_name(self):
        """Requirements: FR-SPM-0096

        The job and its start.
        """
        for run in self:
            run.display_name = " - ".join(filter(None, [
                run.job_id.name, run.date_start and fields.Datetime.to_string(run.date_start)])) or run._description

    @api.depends('errors')
    def _compute_error_count(self):
        for run in self:
            run.error_count = len(run.errors or [])

    @api.depends('errors')
    def _compute_error_summary(self):
        for run in self:
            run.error_summary = '\n'.join(
                '%s: %s' % (entry.get('name') or entry.get('id') or '', entry.get('message', ''))
                for entry in run.errors or [])

    def action_spm_mark_failed(self):
        """Close a run that is stuck as Running (the server stopped during it), so the job can run again.

        Requirements: FR-SPM-0097
        """
        if not self.env.user.has_group('supplier_procurement_management_hub.group_spm_admin'):
            raise AccessError(self.env._("Only a Procurement hub administrator can close a run."))
        self.check_access('read')
        if any(run.state != 'running' for run in self):
            raise UserError(self.env._("Only a running run can be closed."))
        # elevated: the rows have no write access for any role; the administrator role is checked above
        self.sudo().write({'state': 'failed', 'date_end': fields.Datetime.now(),
                           'errors': [{'message': self.env._("Closed by an administrator.")}]})
        return True
