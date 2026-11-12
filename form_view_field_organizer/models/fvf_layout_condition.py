import math

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

NO_VALUE_OPERATORS = ('set', 'not_set')
ORDER_OPERATORS = ('gt', 'lt', 'ge', 'le')
EQUALITY_OPERATORS = ('is', 'is_not')
# Comparisons offered per kind of field (the administrator's answer: dates and
# multi-value links only offer is set / is not set).
OPERATORS_BY_TYPE = {
    'char': EQUALITY_OPERATORS,
    'text': EQUALITY_OPERATORS,
    'selection': EQUALITY_OPERATORS,
    'boolean': EQUALITY_OPERATORS,
    'many2one': EQUALITY_OPERATORS,
    'integer': EQUALITY_OPERATORS + ORDER_OPERATORS,
    'float': EQUALITY_OPERATORS + ORDER_OPERATORS,
    'monetary': EQUALITY_OPERATORS + ORDER_OPERATORS,
}


class FvfLayoutCondition(models.Model):
    _name = 'fvf.layout.condition'
    _description = 'Form layout condition'
    _order = 'sequence, id'

    line_id = fields.Many2one(
        'fvf.layout.line', string='Field line', required=True, ondelete='cascade', index=True)
    sequence = fields.Integer(default=10)
    field_name = fields.Char(string='Field name', required=True)
    field_id = fields.Many2one(
        'ir.model.fields', string='Field record', ondelete='set null',
        compute='_compute_field_id', store=True, readonly=False)
    operator = fields.Selection([
        ('is', 'is'), ('is_not', 'is not'), ('set', 'is set'), ('not_set', 'is not set'),
        ('gt', 'greater than'), ('lt', 'less than'), ('ge', 'at least'), ('le', 'at most'),
    ], string='Comparison', required=True)
    value = fields.Char()
    value_res_id = fields.Many2oneReference(model_field='value_model')
    value_model = fields.Char(compute='_compute_value_model', store=True)
    missing = fields.Boolean(compute='_compute_missing')
    layout_model = fields.Char(related='line_id.layout_id.model', string='Layout model name')

    @api.depends('field_name', 'line_id.layout_id.model')
    def _compute_field_id(self) -> None:
        for condition in self:
            model = condition.line_id.layout_id.model
            condition.field_id = self.env['ir.model.fields']._get(model, condition.field_name) \
                if model and condition.field_name else self.env['ir.model.fields']

    @api.depends('field_id')
    def _compute_value_model(self) -> None:
        for condition in self:
            condition.value_model = condition.field_id.relation if condition.field_id.ttype == 'many2one' else False

    @api.depends('field_id')
    def _compute_missing(self) -> None:
        for condition in self:
            condition.missing = not condition.field_id

    def _fvf_describe(self) -> str:
        self.ensure_one()
        operator = dict(self._fields['operator'].selection)[self.operator]
        parts = [self.field_id.field_description or self.field_name, operator]
        if self.operator not in NO_VALUE_OPERATORS:
            parts.append(self.value or '')
        return " ".join(parts)

    def _fvf_value_is_readable(self) -> bool:
        """Tell whether the value can be read for the kind of the chosen field."""
        self.ensure_one()
        field_type = self.field_id.ttype
        value = (self.value or '').strip()
        if field_type == 'many2one':
            record_id = self.value_res_id or (int(value) if value.isdecimal() else 0)
            return bool(record_id and self.env[self.field_id.relation].browse(record_id).exists())
        if field_type == 'integer':
            return value.lstrip('-').isdecimal()
        if field_type in ('float', 'monetary'):
            try:
                return math.isfinite(float(value))
            except ValueError:
                return False
        if field_type == 'boolean':
            return value.lower() in ('true', 'false', '1', '0')
        if field_type == 'selection':
            field = self.env[self.layout_model]._fields[self.field_name]
            return value in dict(field._description_selection(self.env))
        return bool(value)

    @api.constrains('field_name', 'operator', 'value', 'value_res_id', 'line_id')
    def _check_condition(self) -> None:
        """Refuse a condition the layout cannot honour, each with its own message.

        Requirements: FR-FVF-0016, FR-FVF-0017
        """
        for condition in self:
            line = condition.line_id
            if line.is_required:
                raise ValidationError(_("A required field cannot be hidden."))
            if condition.field_name == line.field_name:
                raise ValidationError(_("A field cannot be shown or hidden based on its own value."))
            if not condition.field_id.store:
                raise ValidationError(_("Choose a field of this form."))
            field_type = condition.field_id.ttype
            if condition.operator not in NO_VALUE_OPERATORS + OPERATORS_BY_TYPE.get(field_type, ()):
                raise ValidationError(_("This comparison cannot be used with this kind of field."))
            if condition.operator not in NO_VALUE_OPERATORS and not condition._fvf_value_is_readable():
                raise ValidationError(_("Enter a value for this comparison."))

    @api.onchange('field_id')
    def _onchange_field_id(self) -> None:
        """The chosen field fills the field name."""
        if self.field_id:
            self.field_name = self.field_id.name

    @api.onchange('value_res_id')
    def _onchange_value_res_id(self) -> None:
        """The chosen record fills the value."""
        if self.value_res_id:
            self.value = str(self.value_res_id)

    @api.onchange('field_name')
    def _onchange_field_name(self) -> dict | None:
        """Warn at once about a condition on the line's own field.

        Requirements: FR-FVF-0017
        """
        if self.field_name and self.field_name == self.line_id.field_name:
            self.field_name = False
            return {'warning': {
                'title': _("Not allowed"),
                'message': _("A field cannot be shown or hidden based on its own value."),
            }}
        return None
