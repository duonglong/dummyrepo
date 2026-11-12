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
# Spellings of yes/no the layout engine reads (engine/expressions.py BOOLEAN_TEXTS).
BOOLEAN_TEXTS = ('1', 'true', 'True', '0', 'false', 'False')


def _is_integer_text(text: str) -> bool:
    try:
        int(text)
    except ValueError:
        return False
    return True


def _is_finite_number_text(text: str) -> bool:
    try:
        return math.isfinite(float(text))
    except ValueError:
        return False


def _is_boolean_text(text: str) -> bool:
    return text in BOOLEAN_TEXTS


# Save-time readers of a value, one per kind of field: each accepts exactly the
# raw text the layout engine parses when it builds the show rule, so a saved
# condition never makes the engine drop the layout.
VALUE_READERS = {
    'integer': _is_integer_text,
    'float': _is_finite_number_text,
    'monetary': _is_finite_number_text,
    'boolean': _is_boolean_text,
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
    refusal_notice = fields.Char(
        string='Refusal', readonly=True, store=False, copy=False,
        help="Why the last change in this row was refused; shown beside the row.")
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

    @api.model_create_multi
    def create(self, vals_list: list[dict]) -> 'FvfLayoutCondition':
        """Requirements: FR-FVF-0014 (a published layout refuses new conditions)"""
        self.env['fvf.layout.line'].browse([vals['line_id'] for vals in vals_list if vals.get('line_id')]).layout_id._fvf_check_unlocked()
        for vals in vals_list:
            self._fvf_value_from_record(vals)
        return super().create(vals_list)

    def write(self, vals: dict) -> bool:
        """Requirements: FR-FVF-0014"""
        self.line_id.layout_id._fvf_check_unlocked()
        self.env['fvf.layout.line'].browse(vals.get('line_id')).layout_id._fvf_check_unlocked()
        self._fvf_value_from_record(vals)
        return super().write(vals)

    def unlink(self) -> bool:
        """Requirements: FR-FVF-0014"""
        self.line_id.layout_id._fvf_check_unlocked()
        return super().unlink()

    @staticmethod
    def _fvf_value_from_record(vals: dict) -> None:
        """A chosen record is the one source of a single link's value: the value is its id."""
        if vals.get('value_res_id'):
            vals['value'] = str(vals['value_res_id'])

    def _fvf_value_record(self) -> models.BaseModel:
        """Return the existing record a single-link value names, or an empty recordset."""
        self.ensure_one()
        Target = self.env[self.field_id.relation]
        value = self.value or ''
        if not _is_integer_text(value):
            return Target
        if self.value_res_id and self.value_res_id != int(value):
            return Target
        return Target.browse(int(value)).exists()

    def _fvf_describe(self) -> str:
        self.ensure_one()
        operator = dict(self._fields['operator'].selection)[self.operator]
        parts = [self.field_id.field_description or self.field_name, operator]
        if self.operator in NO_VALUE_OPERATORS:
            return " ".join(parts)
        if self.field_id.ttype == 'many2one':
            parts.append(self._fvf_value_record().display_name or self.value or '')
        else:
            parts.append(self.value or '')
        return " ".join(parts)

    def _fvf_value_is_readable(self) -> bool:
        """Tell whether the layout engine can read the raw value for the kind of the chosen field."""
        self.ensure_one()
        field_type = self.field_id.ttype
        value = self.value or ''
        if field_type == 'many2one':
            return bool(self._fvf_value_record())
        if field_type == 'selection':
            field = self.env[self.layout_model]._fields[self.field_name]
            return value in dict(field._description_selection(self.env))
        reader = VALUE_READERS.get(field_type)
        return reader(value) if reader else bool(value)

    @api.constrains('field_name', 'field_id', 'operator', 'value', 'value_res_id', 'line_id')
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
            field = condition.field_id
            if not field.store or field.name != condition.field_name \
                    or field.model != condition.layout_model:
                raise ValidationError(_("Choose a field of this form."))
            field_type = field.ttype
            if condition.operator not in NO_VALUE_OPERATORS + OPERATORS_BY_TYPE.get(field_type, ()):
                raise ValidationError(_("This comparison cannot be used with this kind of field."))
            if condition.operator not in NO_VALUE_OPERATORS and not condition._fvf_value_is_readable():
                raise ValidationError(_("Enter a value for this comparison."))

    @api.onchange('field_id')
    def _onchange_field_id(self) -> None:
        """The chosen field fills the field name; a value chosen for another field is cleared."""
        self.value = False
        self.value_res_id = False
        if self.operator not in NO_VALUE_OPERATORS + OPERATORS_BY_TYPE.get(self.field_id.ttype, ()):
            self.operator = False
        if self.field_id:
            self.field_name = self.field_id.name

    @api.onchange('value_res_id')
    def _onchange_value_res_id(self) -> None:
        """The chosen record fills the value."""
        if self.value_res_id:
            self.value = str(self.value_res_id)

    @api.onchange('field_name')
    def _onchange_field_name(self) -> None:
        """Refuse at once, beside the row, a condition on the line's own field.

        Requirements: FR-FVF-0017
        """
        if self.field_name and self.field_name == self.line_id.field_name:
            self.field_name = False
            self.field_id = False
            self.refusal_notice = _("A field cannot be shown or hidden based on its own value.")
