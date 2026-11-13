from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

from ..services import audit
from ..services import correction as engine

ADMIN_GROUP = 'data_governance_quality_center_app.group_dgq_admin'

#: what a correction does: a change of any of these needs a new dry run and a new approval
DEFINITION_FIELDS = (
    'model_id', 'field_id', 'rule_id', 'action', 'default_value', 'text_case', 'link_from_id',
    'link_to_id', 'domain', 'company_ids', 'protected_authorized',
)


class DataQualityCorrection(models.Model):
    """A repeatable fix of one field of a model, with dry runs and an approval.

    Requirements: BR-DGQ-0006, FR-DGQ-0046, FR-DGQ-0047, FR-DGQ-0048
    """
    _name = 'data.quality.correction'
    _description = 'Data Quality Correction'
    _order = 'name, id'

    name = fields.Char(string='Name', required=True)
    model_id = fields.Many2one(
        'ir.model', string='Model', required=True, ondelete='cascade', index=True,
        domain=lambda self: self.env['data.quality.rule']._dgq_model_domain())
    field_id = fields.Many2one(
        'ir.model.fields', string='Field', required=True, ondelete='cascade',
        domain="[('model_id', '=', model_id), ('store', '=', True)]")
    rule_id = fields.Many2one(
        'data.quality.rule', string='Rule', ondelete='set null',
        domain="[('model_id', '=', model_id)]",
        help="The correction fixes the records this rule found.")
    action = fields.Selection(
        selection=lambda self: self.env['data.quality.registry']._dgq_selection('correction_actions'),
        string='Action', required=True)
    default_value = fields.Char(string='Default Value')
    text_case = fields.Selection(
        [('upper', 'Upper case'), ('lower', 'Lower case'), ('title', 'Title case')],
        string='Text Case', default='title')
    link_from_id = fields.Integer(string='Replace Link To Record')
    link_to_id = fields.Integer(string='By Link To Record')
    domain = fields.Char(
        string='Filter', help="Only records matching this filter are corrected, for example "
        "[('country_id', '=', False)].")
    company_ids = fields.Many2many('res.company', string='Companies')
    mapping_ids = fields.One2many(
        'data.quality.correction.mapping', 'correction_id', string='Country and State Mappings')
    state = fields.Selection(
        [('draft', 'Draft'), ('ready', 'Ready'), ('to_approve', 'To Approve'),
         ('approved', 'Approved')],
        string='State', default='draft', required=True, readonly=True, copy=False, index=True)
    needs_approval = fields.Boolean(
        string='Needs Approval', compute='_compute_needs_approval',
        help="A critical rule, a sensitive field or protected records need a second person.")
    author_id = fields.Many2one(
        'res.users', string='Author', default=lambda self: self.env.user, readonly=True,
        copy=False, ondelete='set null')
    approver_id = fields.Many2one(
        'res.users', string='Approved By', readonly=True, copy=False, ondelete='set null')
    approval_date = fields.Datetime(string='Approved On', readonly=True, copy=False)
    approved_dry_run_id = fields.Many2one(
        'data.quality.correction.run', string='Approved Dry Run', readonly=True, copy=False,
        ondelete='set null')
    protected_authorized = fields.Boolean(
        string='Protected Records Authorized', copy=False,
        help="Only an administrator can authorize a correction to change protected records.")
    last_dry_run_id = fields.Many2one(
        'data.quality.correction.run', string='Last Dry Run', readonly=True, copy=False,
        ondelete='set null')
    run_ids = fields.One2many('data.quality.correction.run', 'correction_id', string='Runs')

    # ---- policy ----------------------------------------------------------

    @api.constrains('model_id', 'field_id', 'action', 'rule_id', 'default_value')
    def _check_policy(self):
        """Each action accepts only fields of a fitting kind, of the correction's model.

        Requirements: FR-DGQ-0046
        """
        for correction in self:
            field = correction.field_id
            if field.model_id != correction.model_id:
                raise ValidationError(self.env._(
                    "%(field)s is not a field of %(model)s.",
                    field=field.display_name, model=correction.model_id.name))
            if not field.store:
                raise ValidationError(self.env._(
                    "%s is not stored: only stored fields can be corrected.", field.display_name))
            # sudo: the marks are an administrator setting every author must respect
            if self.env['data.quality.field.config'].sudo().search_count(
                    [('field_id', '=', field.id), ('status', '=', 'excluded')]):
                raise ValidationError(self.env._(
                    "%s is excluded from data quality work and cannot be corrected.",
                    field.display_name))
            if correction.rule_id and correction.rule_id.model_id != correction.model_id:
                raise ValidationError(self.env._("The rule must be a rule of %s.", correction.model_id.name))
            descriptor = self.env['data.quality.registry']._dgq_find('correction_actions', correction.action)
            if not descriptor:
                raise ValidationError(self.env._("This action is not available."))
            if field.ttype not in descriptor['field_types']:
                raise ValidationError(self._dgq_kind_message(correction.action, descriptor))
            if correction.action == 'default':
                try:
                    engine.convert_default(field.ttype, correction.default_value or '')
                except ValueError as error:
                    raise ValidationError(self.env._(
                        "The default value does not fit %s.", field.display_name)) from error

    def _dgq_kind_message(self, key, descriptor):
        if key in ('trim', 'normalize_email', 'normalize_phone', 'text_case'):
            return self.env._("%s applies to text fields only.", descriptor['label'])
        if key == 'replace_link':
            return self.env._("%s applies to link fields only.", descriptor['label'])
        return self.env._("%s does not apply to this kind of field.", descriptor['label'])

    # ---- approval need ---------------------------------------------------

    def _dgq_is_protected(self):
        """Tell whether the records of the model are protected (a restricted model)."""
        self.ensure_one()
        # sudo: a manager may not read the model list, the mark is a setting of the administrator
        return self.env['data.quality.model.config']._get_status(self.model_id.sudo().model) == 'restricted'

    def _dgq_is_sensitive(self):
        """Tell whether the corrected field is marked sensitive, by a mark or by default."""
        self.ensure_one()
        # sudo: the marks are an administrator setting that every author must respect
        config = self.env['data.quality.field.config'].sudo()
        mark = config.search([('field_id', '=', self.field_id.id)]).status
        return mark == 'sensitive' or (not mark and config._is_sensitive_by_default(self.field_id.sudo()))  # sudo: reading the field's own name and groups

    @api.depends('rule_id.severity', 'field_id', 'model_id', 'protected_authorized')
    def _compute_needs_approval(self):
        """Not stored: a mark changed later must count at once.

        Requirements: BR-DGQ-0006
        """
        for correction in self:
            correction.needs_approval = bool(
                correction.rule_id.severity == 'critical'
                or correction.protected_authorized
                or (correction.model_id and correction._dgq_is_protected())
                or (correction.field_id and correction._dgq_is_sensitive()))

    # ---- definition changes ----------------------------------------------

    def _dgq_back_to_draft(self):
        """Put corrections that are not in draft back to draft: a new definition needs new proof.

        Requirements: FR-DGQ-0048
        """
        changed = self.filtered(lambda correction: correction.state != 'draft' or correction.last_dry_run_id)
        if changed:
            changed.write({
                'state': 'draft', 'approver_id': False, 'approval_date': False,
                'approved_dry_run_id': False, 'last_dry_run_id': False,
            })

    def _dgq_check_authorization(self, vals):
        if vals.get('protected_authorized') and not (
                self.env.su or self.env.user.has_group(ADMIN_GROUP)):
            raise UserError(self.env._(
                "Only an administrator can authorize a correction to change protected records."))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            self._dgq_check_authorization(vals)
        return super().create(vals_list)

    def write(self, vals):
        """A change of what the correction does sends it back to draft and removes its approval.

        Requirements: FR-DGQ-0048
        """
        self._dgq_check_authorization(vals)
        if 'state' not in vals and any(name in vals for name in DEFINITION_FIELDS):
            vals = dict(
                vals, state='draft', approver_id=False, approval_date=False,
                approved_dry_run_id=False, last_dry_run_id=False)
        return super().write(vals)

    # ---- buttons ---------------------------------------------------------

    def action_dry_run(self):
        """Compute the before and after values of the targets, writing nothing.

        Requirements: FR-DGQ-0047
        """
        self.ensure_one()
        run = self.env['data.quality.correction.run'].create(
            {'correction_id': self.id, 'kind': 'dry_run'})
        self.last_dry_run_id = run
        run.action_start()
        return True

    def action_approve(self):
        """Approve after the dry run: another manager than the author, or an administrator.

        Requirements: BR-DGQ-0006, FR-DGQ-0048
        """
        self.ensure_one()
        is_admin = self.env.user.has_group(ADMIN_GROUP)
        if not self.needs_approval:
            raise UserError(self.env._("This correction does not need an approval."))
        if self.state != 'to_approve':
            raise UserError(self.env._("Run a dry run and wait for it to end before approving."))
        if self.author_id == self.env.user and not is_admin:
            raise UserError(self.env._(
                "A correction must be approved by another manager or an administrator."))
        self.write({
            'state': 'approved', 'approver_id': self.env.user.id,
            'approval_date': fields.Datetime.now(), 'approved_dry_run_id': self.last_dry_run_id.id,
        })
        audit.log(self.env, 'correction_approved', self)
        return True

    def action_run(self):
        """Run the correction for real, after a dry run and the approval it needs.

        Requirements: BR-DGQ-0006, FR-DGQ-0047, FR-DGQ-0048
        """
        self.ensure_one()
        if self.last_dry_run_id.state != 'done':
            raise UserError(self.env._("Run a dry run first and wait for it to end."))
        if self.needs_approval and self.state != 'approved':
            raise UserError(self.env._(
                "This correction waits for approval by another manager or an administrator."))
        run = self.env['data.quality.correction.run'].create(
            {'correction_id': self.id, 'kind': 'run'})
        run.action_start()
        audit.log(self.env, 'correction_run', self)
        return True

    def _dgq_dry_run_done(self, run):
        """A finished dry run makes a draft correction ready, or waiting for approval."""
        for correction in self:
            if correction.state == 'draft' and correction.last_dry_run_id == run:
                correction.write({'state': 'to_approve' if correction.needs_approval else 'ready'})


class DataQualityCorrectionRun(models.Model):
    """One dry run or real run of a correction, with its counts and its log.

    Requirements: FR-DGQ-0047
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
    res_ids = fields.Json(string='Targets', copy=False)
    last_id = fields.Integer(string='Checkpoint', copy=False)
    count_changed = fields.Integer(string='Changed', copy=False)
    count_unchanged = fields.Integer(string='Unchanged', copy=False)
    count_skipped = fields.Integer(string='Skipped', copy=False)
    count_failed = fields.Integer(string='Failed', copy=False)
    log_ids = fields.One2many('data.quality.correction.log', 'run_id', string='Log')

    def _dgq_run_batch(self):
        """Process the next batch of targets.

        Requirements: FR-DGQ-0047
        """
        return engine.run_batch(self)

    def _dgq_record_batch(self, processed, remaining):
        super()._dgq_record_batch(processed, remaining)
        if self.state == 'done' and self.kind == 'dry_run':
            self.correction_id._dgq_dry_run_done(self)


class DataQualityCorrectionLog(models.Model):
    """One value that a run would change, changed, skipped or failed to change, before and after.

    Requirements: FR-DGQ-0047
    """
    _name = 'data.quality.correction.log'
    _inherit = ['data.quality.result.mixin']
    _description = 'Data Quality Correction Log'
    _order = 'run_id, id'

    run_id = fields.Many2one(
        'data.quality.correction.run', string='Run', required=True, ondelete='cascade', index=True)
    res_id = fields.Many2oneReference(string='Record', model_field='res_model', index=True)
    field_name = fields.Char(string='Field')
    value_before = fields.Json(string='Before')
    value_after = fields.Json(string='After')
    state = fields.Selection(
        [('would_change', 'Would change'), ('changed', 'Changed'), ('skipped', 'Skipped'),
         ('failed', 'Failed'), ('restored', 'Restored'), ('not_restored', 'Not restored')],
        string='Result', required=True, index=True)
    reason = fields.Char(string='Reason')
    user_id = fields.Many2one('res.users', string='User', ondelete='set null')
    date = fields.Datetime(string='Time')
