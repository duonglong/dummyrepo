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

#: set by the buttons only: a user who writes them could skip the dry run or the approval
GUARDED_FIELDS = (
    'state', 'author_id', 'approver_id', 'approval_date', 'approved_dry_run_id', 'last_dry_run_id',
)


def context_without_defaults(env, allowed):
    """The context of ``env`` without the ``default_<field>`` keys of fields outside ``allowed``.

    The ORM fills the fields missing from the values of a create with these keys, after any
    check of the values: a user gives through them only what he may give in the values.
    """
    return {
        key: value for key, value in env.context.items()
        if not key.startswith('default_') or key.removeprefix('default_') in allowed
    }


class DataQualityCorrection(models.Model):
    """A repeatable fix of one field of a model, with dry runs and an approval.

    Requirements: BR-DGQ-0006, FR-DGQ-0046, FR-DGQ-0047, FR-DGQ-0048
    """
    _name = 'data.quality.correction'
    _description = 'Data Quality Correction'
    _order = 'name, id'

    name = fields.Char(string='Name', required=True)
    active = fields.Boolean(default=True)
    model_id = fields.Many2one(
        'ir.model', string='Model', required=True, ondelete='cascade', index=True,
        domain=lambda self: self.env['data.quality.rule']._dgq_model_domain())
    field_id = fields.Many2one(
        'ir.model.fields', string='Field', required=True, ondelete='cascade',
        domain="[('model_id', '=', model_id), ('store', '=', True)]")
    rule_id = fields.Many2one(
        'data.quality.rule', string='Rule', ondelete='restrict',
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
    company_ids = fields.Many2many(
        'res.company', string='Companies', ondelete='restrict',
        help="Empty: all your companies. A company used here cannot be deleted.")
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

    @api.constrains('field_id', 'action', 'default_value', 'link_from_id', 'link_to_id')
    def _check_settings(self):
        """The settings of an action must fit the field, so a run never fails on every record.

        Requirements: FR-DGQ-0046
        """
        for correction in self:
            field = correction.field_id.sudo()  # sudo: a manager may not read the field list
            if correction.action == 'country_state' and field.ttype == 'many2one' and (
                    field.relation not in engine.COUNTRY_MODELS):
                raise ValidationError(self.env._(
                    "Standardize country and state applies to country and state links only."))
            if correction.action == 'default' and field.ttype == 'selection' and (
                    correction.default_value not in self.env[field.model]._fields[field.name].get_values(self.env)):
                raise ValidationError(self.env._(
                    "The default value is not one of the choices of %s.", field.display_name))
            if correction.action == 'replace_link' and field.ttype == 'many2one':
                ids = [link for link in (correction.link_from_id, correction.link_to_id) if link]
                # sudo: only the existence of the linked records is checked
                if len(self.env[field.relation].sudo().browse(ids).exists()) != len(ids):
                    raise ValidationError(self.env._(
                        "The records to replace and to link must exist in %s.", field.relation))

    @api.constrains('domain', 'model_id')
    def _check_domain(self):
        """The filter is checked when it is saved."""
        self._dgq_check_domain()

    def _dgq_check_domain(self):
        """The filter is plain domain text on the model that reaches no field a scan may not read.

        It is searched in sudo, so a filter on a sensitive field would show its value. The marks
        change after the filter was saved: the engine checks again before every search.

        Requirements: BR-DGQ-0009, BR-DGQ-0010
        """
        rules = self.env['data.quality.rule']
        marked = set(rules._dgq_marked_models().mapped('model'))
        for correction in self.filtered('domain'):
            model = self.env[correction.model_id.sudo().model]  # sudo: a manager may not read the model list
            try:
                domain = engine.parse_domain(model, correction.domain)
            except (ValueError, SyntaxError, TypeError, KeyError, AttributeError, UserError) as error:
                raise ValidationError(self.env._(
                    "The filter of %(name)s is not a valid domain on %(model)s: %(error)s",
                    name=correction.name, model=correction.model_id.name, error=error)) from error
            blocked = sorted(rules._dgq_blocked_fields(model, domain, {}, marked))
            if blocked:
                raise ValidationError(self.env._(
                    "The filter of %(name)s uses fields that are not stored, are excluded or "
                    "sensitive, or lead to a model not marked supported or restricted: %(fields)s. "
                    "Compare a link with record ids.", name=correction.name,
                    fields=', '.join(blocked)))

    def _dgq_kind_message(self, key, descriptor):
        kinds = set(descriptor['field_types'])
        if kinds <= set(engine.TEXT_TYPES):
            return self.env._("%s applies to text fields only.", descriptor['label'])
        if kinds <= set(engine.LINK_TYPES):
            return self.env._("%s applies to link fields only.", descriptor['label'])
        return self.env._("%s does not apply to this kind of field.", descriptor['label'])

    # ---- approval need ---------------------------------------------------

    def _dgq_is_protected(self):
        """Tell whether the records of the model are protected: an accounting model or a restricted one.

        Requirements: BR-DGQ-0005
        """
        self.ensure_one()
        model_name = self.model_id.sudo().model  # sudo: a manager may not read the model list
        return (model_name in engine.ACCOUNTING_MODELS
                or self.env['data.quality.model.config']._get_status(model_name) == 'restricted')

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
            changed.sudo().write({  # sudo: the reset is the app's, users may not write these fields
                'state': 'draft', 'approver_id': False, 'approval_date': False,
                'approved_dry_run_id': False, 'last_dry_run_id': False,
            })

    def _dgq_check_authorization(self, vals):
        if vals.get('protected_authorized') and not (
                self.env.su or self.env.user.has_group(ADMIN_GROUP)):
            raise UserError(self.env._(
                "Only an administrator can authorize a correction to change protected records."))

    def _dgq_check_guarded(self, vals):
        """Refuse the fields only the buttons set; the app writes them in sudo."""
        if not self.env.su and any(name in vals for name in GUARDED_FIELDS):
            raise UserError(self.env._(
                "The state, author, approval and dry run of a correction are set by its buttons only."))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            self._dgq_check_authorization(vals)
            self._dgq_check_guarded(vals)
        if not self.env.su:
            allowed = set(self._fields) - set(GUARDED_FIELDS)
            if not self.env.user.has_group(ADMIN_GROUP):
                allowed.discard('protected_authorized')
            self = self.with_context(context_without_defaults(self.env, allowed))
        return super().create(vals_list)

    def write(self, vals):
        """A change of what the correction does sends it back to draft and removes its approval.

        Requirements: BR-DGQ-0006, FR-DGQ-0048
        """
        self._dgq_check_authorization(vals)
        self._dgq_check_guarded(vals)
        if any(name in vals for name in DEFINITION_FIELDS):
            vals = dict(
                vals, state='draft', approver_id=False, approval_date=False,
                approved_dry_run_id=False, last_dry_run_id=False)
        return super().write(vals)

    def unlink(self):
        """A correction that ran for real is the proof of what was changed: it is archived."""
        if self.run_ids.sudo().filtered(  # sudo: runs of any user count
                lambda run: run.kind != 'dry_run'):
            raise UserError(self.env._(
                "This correction has run: archive it instead of deleting it."))
        return super().unlink()

    # ---- buttons ---------------------------------------------------------

    def action_dry_run(self):
        """Compute the before and after values of the targets, writing nothing.

        Requirements: FR-DGQ-0047
        """
        self.ensure_one()
        self.check_access('write')  # the button then writes in sudo
        run = self.env['data.quality.correction.run'].create(
            {'correction_id': self.id, 'kind': 'dry_run'})
        self.sudo().last_dry_run_id = run  # sudo: the button sets a field users may not write
        run.action_start()
        return True

    def action_approve(self):
        """Approve after the dry run: another manager than the author, or an administrator.

        Requirements: BR-DGQ-0006, FR-DGQ-0048
        """
        self.ensure_one()
        self.check_access('write')  # the button then writes in sudo
        is_admin = self.env.user.has_group(ADMIN_GROUP)
        if not self.needs_approval:
            raise UserError(self.env._("This correction does not need an approval."))
        if self.state != 'to_approve':
            raise UserError(self.env._("Run a dry run and wait for it to end before approving."))
        if self.author_id == self.env.user and not is_admin:
            raise UserError(self.env._(
                "A correction must be approved by another manager or an administrator."))
        self.sudo().write({  # sudo: the button sets the approval, users may not write it
            'state': 'approved', 'approver_id': self.env.user.id,
            'approval_date': fields.Datetime.now(), 'approved_dry_run_id': self.last_dry_run_id.id,
        })
        audit.log(self.env, 'correction_approved', self)
        return True

    def _dgq_check_real_run_allowed(self, new_run=True):
        """Raise when a real run is not allowed: a dry run first, then the approval it needs.

        ``new_run`` is false while a run is going on: the dry run it followed is already behind it.

        Requirements: BR-DGQ-0006, FR-DGQ-0047, FR-DGQ-0048
        """
        self.ensure_one()
        if new_run and self.last_dry_run_id.state != 'done':
            raise UserError(self.env._("Run a dry run first and wait for it to end."))
        if self.state == 'draft':
            raise UserError(self.env._("Run a dry run first and wait for it to end."))
        if self.needs_approval and self.state != 'approved':
            raise UserError(self.env._(
                "This correction waits for approval by another manager or an administrator."))

    def action_run(self):
        """Run the correction for real, after a dry run and the approval it needs.

        Requirements: BR-DGQ-0006, FR-DGQ-0047, FR-DGQ-0048
        """
        self.ensure_one()
        self.check_access('write')  # the button then writes in sudo
        run = self.env['data.quality.correction.run'].create(
            {'correction_id': self.id, 'kind': 'run'})
        run.action_start()
        audit.log(self.env, 'correction_run', self)
        return True

    def _dgq_dry_run_done(self, run):
        """A finished dry run makes the correction ready, or waiting for approval when it needs one."""
        for correction in self.filtered(
                lambda c: c.state in ('draft', 'ready', 'to_approve') and c.last_dry_run_id == run):
            correction.sudo().write({'state': 'to_approve' if correction.needs_approval else 'ready'})  # sudo: set by the engine
