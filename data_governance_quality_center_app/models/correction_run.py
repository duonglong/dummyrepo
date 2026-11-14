from odoo import api, fields, models
from odoo.exceptions import UserError

from ..services import audit
from ..services import correction as engine
from .correction import context_without_defaults

#: the fields a user may give a run when creating it; the others are set by the buttons and the engine
RUN_USER_FIELDS = frozenset({'correction_id', 'kind'})


class DataQualityCorrectionRun(models.Model):
    """One dry run or real run of a correction, with its counts and its log.

    Requirements: FR-DGQ-0047, FR-DGQ-0050
    """
    _name = 'data.quality.correction.run'
    _inherit = ['data.quality.job.mixin']
    _description = 'Data Quality Correction Run'
    _order = 'id desc'
    _dgq_cron_xmlid = 'data_governance_quality_center_app.ir_cron_dgq_correction'

    correction_id = fields.Many2one(
        'data.quality.correction', string='Correction', required=True, ondelete='cascade', index=True)
    kind = fields.Selection(
        [('dry_run', 'Dry run'), ('run', 'Run'), ('rollback', 'Rollback')],
        string='Kind', required=True, readonly=True)
    dry_run_id = fields.Many2one(
        'data.quality.correction.run', string='Started After Dry Run', readonly=True, copy=False,
        ondelete='set null', help="A real run goes on only while this is the correction's last dry run.")
    res_ids = fields.Json(string='Targets', copy=False)
    last_id = fields.Integer(string='Checkpoint', copy=False)
    count_changed = fields.Integer(string='Changed', copy=False)
    count_unchanged = fields.Integer(string='Unchanged', copy=False)
    count_skipped = fields.Integer(string='Skipped', copy=False)
    count_failed = fields.Integer(string='Failed', copy=False)
    count_restored = fields.Integer(string='Restored', copy=False)
    count_not_restored = fields.Integer(string='Not restored', copy=False)
    rollback_of_id = fields.Many2one(
        'data.quality.correction.run', string='Rollback Of', readonly=True, copy=False,
        ondelete='set null', index=True)
    rolled_back = fields.Boolean(string='Rolled Back', readonly=True, copy=False)
    log_ids = fields.One2many('data.quality.correction.log', 'run_id', string='Log')

    def _dgq_check_real_run(self, new_run=True):
        """Raise when this real run may not go on: the correction's checks, then the dry run it is bound to.

        Requirements: BR-DGQ-0006, FR-DGQ-0048
        """
        self.ensure_one()
        self.correction_id._dgq_check_real_run_allowed(new_run=new_run)
        if self.dry_run_id and self.dry_run_id != self.correction_id.last_dry_run_id:
            raise UserError(self.env._(
                "The correction changed or was dry-run again since this run started: start a new run."))

    def _dgq_check_start(self):
        """A real run needs the dry run and the approval, and one real run at a time.

        Requirements: BR-DGQ-0006, FR-DGQ-0048
        """
        for run in self:
            run.correction_id._dgq_check_domain()
        for run in self.filtered(lambda r: r.kind == 'run'):
            run._dgq_check_real_run()
            # the row lock makes two requests start one after the other
            self.env.cr.execute(
                "SELECT id FROM data_quality_correction WHERE id = %s FOR UPDATE", [run.correction_id.id])
            # sudo: any queued or running run of the correction counts, whoever started it
            if self.env[run._name].sudo().search_count([
                    ('correction_id', '=', run.correction_id.id), ('kind', '=', 'run'),
                    ('state', 'in', ('queued', 'running')), ('id', '!=', run.id)]):
                raise UserError(self.env._(
                    "This correction is already running: wait for the run to end or cancel it."))

    def _dgq_bind_dry_run(self):
        """Bind each real run to the correction's last dry run when it is first queued."""
        for run in self.filtered(lambda r: r.kind == 'run' and not r.dry_run_id):
            run.sudo().dry_run_id = run.correction_id.last_dry_run_id  # sudo: set by the app only

    def action_start(self):
        """Queue the run as the user who may write it; the app then writes the run's fields.

        Requirements: BR-DGQ-0006, FR-DGQ-0048
        """
        self.check_access('write')
        self._dgq_check_start()
        self._dgq_bind_dry_run()
        return super(DataQualityCorrectionRun, self.sudo()).action_start()  # sudo: the app sets the run's fields

    def action_resume(self):
        """Queue a failed run again, only while it is bound to the correction's last dry run.

        Requirements: BR-DGQ-0006, FR-DGQ-0048
        """
        self.check_access('write')
        self._dgq_check_start()
        self._dgq_bind_dry_run()
        return super(DataQualityCorrectionRun, self.sudo()).action_resume()  # sudo: the app sets the run's fields

    def action_rollback(self):
        """Queue the rollback of this run: it restores the values that still hold what the run wrote.

        Requirements: BR-DGQ-0007, FR-DGQ-0050
        """
        self.ensure_one()
        self.check_access('write')  # the rollback is then created in sudo
        if self.kind != 'run' or self.state != 'done':
            raise UserError(self.env._("Only a finished run can be rolled back."))
        # sudo: a rollback of any user counts, and the app creates the rollback run
        runs = self.sudo()
        if runs.rolled_back or runs.search_count([
                ('rollback_of_id', '=', self.id), ('state', 'in', ('queued', 'running'))]):
            raise UserError(self.env._("This run was already rolled back or is being rolled back."))
        rollback = runs.create({
            'correction_id': self.correction_id.id, 'kind': 'rollback', 'rollback_of_id': self.id})
        rollback.action_start()
        audit.log(self.env, 'correction_rolled_back', self.correction_id)
        return True

    def action_cancel(self):
        self.check_access('write')
        return super(DataQualityCorrectionRun, self.sudo()).action_cancel()  # sudo: the app sets the run's fields

    @api.model_create_multi
    def create(self, vals_list):
        """A user gives a run its correction and kind only: the engine trusts every other field."""
        if not self.env.su:
            if any(set(vals) - RUN_USER_FIELDS for vals in vals_list):
                raise UserError(self.env._("A run is started by the buttons of its correction only."))
            self = self.with_context(context_without_defaults(self.env, RUN_USER_FIELDS))
        return super().create(vals_list)

    def write(self, vals):
        """No field of a run is written by a user: the buttons and the engine write them."""
        if not self.env.su and vals:
            raise UserError(self.env._("A run is started by the buttons of its correction only."))
        return super().write(vals)

    def _dgq_run_batch(self):
        """Process the next batch of targets; a real run stops when its approval is gone.

        Requirements: BR-DGQ-0006, FR-DGQ-0047, FR-DGQ-0048
        """
        if self.kind == 'rollback':
            return engine.rollback_batch(self)
        if self.kind == 'run':
            try:
                self._dgq_check_real_run(new_run=False)
            except UserError as refusal:
                self.write({
                    'state': 'failed', 'date_end': fields.Datetime.now(),
                    'error_message': self.env._("Stopped: the correction changed. %s", refusal)})
                return 0, 0
        return engine.run_batch(self)

    def _dgq_record_batch(self, processed, remaining):
        if self.state == 'failed':
            return
        super()._dgq_record_batch(processed, remaining)
        if self.state == 'done' and self.kind == 'dry_run':
            self.correction_id._dgq_dry_run_done(self)
        if self.state == 'done' and self.kind == 'rollback':
            self.rollback_of_id.sudo().write({'rolled_back': True})  # sudo: set by the engine
