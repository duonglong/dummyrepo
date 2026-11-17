from odoo import Command, api, fields, models
from odoo.exceptions import UserError, ValidationError

from ..const import PROFILE_STALE_DAYS
from ..services import profile_compare, profiling
from .job_mixin import DataQualityJobMixin


class DataQualityProfile(models.Model):
    """A dated profile of the fields of a model for a company, computed in the background.

    The job computes one field per batch (the checkpoint is the index of the next field) and
    never changes the profiled data. With no field chosen, every usable field is profiled.

    Requirements: FR-DGQ-0053, FR-DGQ-0054, FR-DGQ-0055
    """
    _name = 'data.quality.profile'
    _inherit = ['data.quality.job.mixin', 'data.quality.result.mixin']
    _dgq_app_fields = DataQualityJobMixin._dgq_app_fields | {
        'res_model', 'record_count', 'stale_count', 'next_field_index'}
    _dgq_scope_fields = frozenset({'model_id', 'company_id', 'field_ids', 'stale_days'})
    _description = 'Data Quality Profile'
    _order = 'id desc'
    _dgq_cron_xmlid = 'data_governance_quality_center_app.ir_cron_dgq_profile'

    model_id = fields.Many2one(
        'ir.model', string='Model', required=True, ondelete='cascade', index=True,
        domain=lambda self: self.env['data.quality.rule']._dgq_model_domain(),
        help="The model to profile. Only the models marked supported or restricted are offered.")
    res_model = fields.Char(related='model_id.model', store=True, readonly=True, required=False)
    company_id = fields.Many2one(
        required=True, ondelete='cascade', default=lambda self: self.env.company,
        help="The profile counts the records of this company.")
    field_ids = fields.Many2many(
        'ir.model.fields', string='Fields', copy=False,
        help="The fields to profile. Leave empty to profile every field that is not excluded or sensitive.")
    allowed_field_ids = fields.Many2many(
        'ir.model.fields', compute='_compute_allowed_field_ids', string='Fields Offered')
    stale_days = fields.Integer(
        string='Stale After (days)', default=PROFILE_STALE_DAYS,
        help="A record not updated for more than this many days is stale.")
    record_count = fields.Integer(copy=False, readonly=True)
    stale_count = fields.Integer(copy=False, readonly=True)
    compare_profile_id = fields.Many2one(
        'data.quality.profile', string='Compare With', copy=False, ondelete='set null',
        domain="[('model_id', '=', model_id), ('company_id', '=', company_id), "
               "('state', '=', 'done')] + ([('id', '<', id)] if id else [])",
        help="A finished, earlier profile of the same model and company.")
    next_field_index = fields.Integer(copy=False)
    line_ids = fields.One2many('data.quality.profile.line', 'profile_id', string='Fields Profiled')

    _stale_days_positive = models.Constraint(
        'CHECK(stale_days > 0)', 'The stale limit must be at least one day.')

    # job state and progress are written by the app only
    state = fields.Selection(readonly=True)
    total_count = fields.Integer(readonly=True)
    done_count = fields.Integer(readonly=True)

    @api.depends('model_id', 'company_id', 'create_date')
    def _compute_display_name(self):
        for profile in self:
            profile.display_name = self.env._(
                "%(model)s, %(company)s, %(date)s",
                model=profile.model_id.name or '', company=profile.company_id.name or '',
                date=fields.Date.to_string(profile.create_date or fields.Date.today()))

    @api.depends('model_id')
    def _compute_allowed_field_ids(self):
        """The fields the picker offers: stored in a column, not excluded or sensitive."""
        for profile in self:
            profile.allowed_field_ids = profile._usable_fields() if profile.res_model else False

    @api.constrains('field_ids', 'model_id')
    def _check_fields(self):
        """The fields belong to the model, are stored in a column and are not excluded or sensitive.

        Requirements: FR-DGQ-0053
        """
        usable_names = self.env['data.quality.field.config']
        for profile in self:
            usable = usable_names._get_usable_field_names(profile.res_model)
            for field in profile.field_ids:
                python_field = self.env[profile.res_model]._fields.get(field.name)
                if (field.model_id != profile.model_id or field.name not in usable
                        or python_field is None or not python_field.column_type
                        or python_field.company_dependent):
                    raise ValidationError(self.env._(
                        "The field %(field)s cannot be profiled on %(model)s: it is not stored in the "
                        "model's own table, or it is excluded or sensitive. A data quality "
                        "administrator can mark a field as allowed in Data Quality > "
                        "Configuration > Fields.",
                        field=field.display_name, model=profile.model_id.name))

    @api.constrains('model_id')
    def _check_model_is_marked(self):
        """Only models marked supported or restricted can be profiled.

        Requirements: FR-DGQ-0053
        """
        allowed = self.env['data.quality.rule']._dgq_marked_models()
        for profile in self:
            if profile.model_id not in allowed:
                raise ValidationError(self.env._(
                    "%s is not marked as supported in the data quality settings and cannot be "
                    "profiled. A data quality administrator can mark it in Data Quality > "
                    "Configuration > Models.", profile.model_id.name))

    @api.constrains('compare_profile_id', 'model_id', 'company_id')
    def _check_compare_profile(self):
        """The profile to compare with is an earlier profile of the same model and company.

        It may still be running when chosen, so the comparison dialog is what requires it finished.

        Requirements: FR-DGQ-0056
        """
        for profile in self.filtered('compare_profile_id'):
            other = profile.compare_profile_id
            if other == profile or other.model_id != profile.model_id or other.company_id != profile.company_id:
                raise ValidationError(self.env._(
                    "A profile can only be compared with another profile of the same model and company."))
            if other.id >= profile.id:
                raise ValidationError(self.env._("The profile to compare with must be an earlier profile."))

    @api.onchange('model_id')
    def _onchange_model_id(self):
        self.field_ids = False
        self.compare_profile_id = False

    # ---- job ---------------------------------------------------------------

    def action_start(self):
        """Queue the profile from its first field, dropping the lines of an earlier start.

        Requirements: FR-DGQ-0053
        """
        for profile in self:
            if profile.state not in ('draft', 'failed', 'cancelled'):
                raise UserError(self.env._("Only a draft, failed or cancelled job can be started."))
            if not profile.field_ids:
                # the default list is fixed at the start, so the checkpoint index stays stable
                profile._dgq_write_as_app({'field_ids': [Command.set(profile._usable_fields().ids)]})
            profile.sudo().line_ids.unlink()  # sudo: the lines are written by the app only
        self._dgq_write_as_app({'next_field_index': 0, 'record_count': 0, 'stale_count': 0,
                                'done_count': 0, 'total_count': 0})
        return super().action_start()

    # ---- compare and export ---------------------------------------------------

    def action_compare(self):
        """Open the comparison with the earlier profile chosen on the profile.

        Requirements: FR-DGQ-0056
        """
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._("Compare Profiles"),
            'res_model': 'data.quality.profile.compare.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'active_model': self._name, 'active_id': self.id},
        }

    def _check_exportable(self):
        """Only a finished profile is exported."""
        for profile in self:
            if profile.state != 'done':
                raise UserError(self.env._("Only a finished profile can be exported."))

    def action_export_xlsx(self):
        """Download the profile as an XLSX file, one sheet per kind of statistic.

        Requirements: FR-DGQ-0057
        """
        self.ensure_one()
        self._check_exportable()
        return {
            'type': 'ir.actions.act_url',
            'url': '/data_quality/xlsx/profile/%s' % self.id,
            'target': 'self',
        }

    def action_export_pdf(self):
        """Give the profile as a PDF report with the same figures.

        Requirements: FR-DGQ-0057
        """
        self.ensure_one()
        self._check_exportable()
        return self.env.ref('data_governance_quality_center_app.action_report_profile').report_action(self)

    def get_export_sections(self):
        """The figures of the profile as ``(title, header, rows)`` sections, for the PDF report.

        Requirements: FR-DGQ-0057
        """
        self.ensure_one()
        self._check_exportable()
        return profile_compare.profile_sections(self)

    def get_export_header(self):
        """The model, company, date and users the exports name, as ``(label, value)`` pairs.

        Requirements: FR-DGQ-0057
        """
        self.ensure_one()
        return profile_compare.profile_header(self)

    def _usable_fields(self):
        """Every stored field of the model that is not excluded or sensitive, in a stable order."""
        self.ensure_one()
        model = self.env[self.res_model]
        usable = self.env['data.quality.field.config']._get_usable_field_names(self.res_model)
        names = sorted(
            name for name in usable
            if model._fields.get(name) is not None and model._fields[name].column_type
            and not model._fields[name].company_dependent and name != 'id')
        # sudo: the field list of a model is read as the app
        return self.env['ir.model.fields'].sudo().search(
            [('model', '=', self.res_model), ('name', 'in', names)], order='id')

    def _check_still_allowed(self, field):
        """Refuse the model or the field when a mark set after the profile was saved forbids it.

        Requirements: FR-DGQ-0053, BR-DGQ-0010
        """
        if self.model_id not in self.env['data.quality.rule']._dgq_marked_models():
            raise UserError(self.env._(
                "%s is no longer marked as supported in the data quality settings.", self.model_id.name))
        if field not in self._usable_fields():
            raise UserError(self.env._(
                "The field %(field)s is now excluded or sensitive and cannot be profiled. "
                "Create a new profile without this field.", field=field.display_name))

    def _dgq_run_batch(self):
        """Compute the line of the next field; return ``(processed, remaining)``.

        Requirements: FR-DGQ-0053, FR-DGQ-0054, FR-DGQ-0055
        """
        self.ensure_one()
        # sudo: the job reads the field list as the app, whoever started it
        fields_to_profile = self.sudo().field_ids.sorted('id') or self._usable_fields()
        index = self.next_field_index
        if index == 0:
            self.write({
                'record_count': profiling.count_records(self.env, self.res_model, self.company_id),
                'stale_count': profiling.count_stale(
                    self.env, self.res_model, self.company_id, self.stale_days),
                'total_count': len(fields_to_profile),
            })
        if index >= len(fields_to_profile):
            return 0, 0
        field = fields_to_profile[index]
        try:
            self._check_still_allowed(field)
        except UserError as refusal:
            self.write({
                'state': 'failed', 'date_end': fields.Datetime.now(),
                'error_message': self.env._("Stopped: %s", refusal)})
            return 0, 0
        values = profiling.profile_field(self.env, self.res_model, field.name, self.company_id)
        # sudo: the lines are written by the app only; a resumed job replaces the line of its field
        self.sudo().line_ids.filtered(lambda line: line.field_id == field).unlink()
        # sudo: the lines are written by the app only
        self.env['data.quality.profile.line'].sudo().create(dict(
            values, profile_id=self.id, field_id=field.id, field_name=field.name))
        self.next_field_index = index + 1
        return 1, len(fields_to_profile) - index - 1

    def _dgq_record_batch(self, processed, remaining):
        if self.state == 'failed':
            return
        super()._dgq_record_batch(processed, remaining)


class DataQualityProfileLine(models.Model):
    """The statistics of one field in a profile.

    Requirements: FR-DGQ-0053, FR-DGQ-0054, FR-DGQ-0055
    """
    _name = 'data.quality.profile.line'
    _inherit = ['data.quality.result.mixin']
    _description = 'Data Quality Profile Line'
    _order = 'profile_id, id'

    profile_id = fields.Many2one(
        'data.quality.profile', string='Profile', required=True, ondelete='cascade', index=True)
    res_model = fields.Char(related='profile_id.res_model', store=True, readonly=True, required=False)
    company_id = fields.Many2one(
        related='profile_id.company_id', store=True, readonly=True, ondelete='cascade')
    field_id = fields.Many2one('ir.model.fields', string='Field', required=True, ondelete='cascade')
    field_name = fields.Char(string='Technical Name', required=True)
    field_type = fields.Char(string='Type')
    empty_count = fields.Integer(string='Empty')
    empty_share = fields.Float(string='Empty (%)')
    distinct_count = fields.Integer(string='Distinct Values')
    outlier_count = fields.Integer(string='Unusual Values')
    link_empty_share = fields.Float(string='Empty Links (%)')
    min_value = fields.Float(string='Minimum')
    max_value = fields.Float(string='Maximum')
    mean_value = fields.Float(string='Average')
    median_value = fields.Float(string='Median')
    top_values = fields.Json(string='Most Frequent Values')
    shapes = fields.Json(string='Value Shapes')
    link_distribution = fields.Json(string='Records per Linked Record')
    unused_choices = fields.Json(string='Unused Choices')

    top_values_text = fields.Text(string='Most Frequent Values', compute='_compute_texts')
    shapes_text = fields.Text(string='Value Shapes', compute='_compute_texts')
    link_distribution_text = fields.Text(string='Records per Linked Record', compute='_compute_texts')
    unused_choices_text = fields.Text(string='Unused Choices', compute='_compute_texts')

    @api.depends('top_values', 'shapes', 'link_distribution', 'unused_choices')
    def _compute_texts(self):
        """The stored lists as one line per entry, for the screen."""
        def counted(pairs):
            return '\n'.join('%s: %s' % (key, count) for key, count in pairs or [])
        for line in self:
            line.top_values_text = counted(line.top_values)
            line.shapes_text = counted(line.shapes)
            line.link_distribution_text = counted(line.link_distribution)
            line.unused_choices_text = '\n'.join(line.unused_choices or [])
