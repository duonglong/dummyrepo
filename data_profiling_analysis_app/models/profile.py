from odoo import api, fields, models
from odoo.exceptions import ValidationError

TECHNICAL_FIELDS = ('id', 'create_uid', 'create_date', 'write_uid', 'write_date')
PROFILABLE_TYPES = (
    'char', 'text', 'html', 'integer', 'float', 'monetary', 'boolean',
    'date', 'datetime', 'selection', 'many2one', 'many2many',
)
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
        model = self.env[self.model_name]
        return [
            name for name, field in model._fields.items()
            if field.store
            and name not in TECHNICAL_FIELDS
            and field.type in PROFILABLE_TYPES
            and model._has_field_access(field, 'read')
        ]

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
        return profiles

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
