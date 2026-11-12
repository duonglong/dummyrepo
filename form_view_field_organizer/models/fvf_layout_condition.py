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

    @api.depends('field_name', 'line_id.layout_id.model')
    def _compute_field_id(self):
        for condition in self:
            model = condition.line_id.layout_id.model
            condition.field_id = self.env['ir.model.fields']._get(model, condition.field_name) \
                if model and condition.field_name else self.env['ir.model.fields']

    @api.depends('field_id')
    def _compute_value_model(self):
        for condition in self:
            condition.value_model = condition.field_id.relation if condition.field_id.ttype == 'many2one' else False

    @api.depends('field_id')
    def _compute_missing(self):
        for condition in self:
            condition.missing = not condition.field_id

    def _fvf_describe(self):
        self.ensure_one()
        operator = dict(self._fields['operator'].selection)[self.operator]
        parts = [self.field_id.field_description or self.field_name, operator]
        if self.operator not in NO_VALUE_OPERATORS:
            parts.append(self.value or '')
        return " ".join(parts)

    def _fvf_has_value(self):
        self.ensure_one()
        return bool(self.value or self.value_res_id)

    @api.constrains('field_name', 'operator', 'value', 'value_res_id', 'line_id')
    def _check_condition(self):
        """Refuse a condition the layout cannot honour, each with its own message.

        Requirements: FR-FVF-0016, FR-FVF-0017
        """
        for condition in self:
            line = condition.line_id
            if line.is_required:
                raise ValidationError(_("A required field cannot be hidden."))
            if condition.field_name == line.field_name:
                raise ValidationError(_("A field cannot be shown or hidden based on its own value."))
            field_type = condition.field_id.ttype
            if field_type and condition.operator not in NO_VALUE_OPERATORS + OPERATORS_BY_TYPE.get(field_type, ()):
                raise ValidationError(_("This comparison cannot be used with this kind of field."))
            if condition.operator not in NO_VALUE_OPERATORS and not condition._fvf_has_value():
                raise ValidationError(_("Enter a value for this comparison."))

    @api.onchange('field_name')
    def _onchange_field_name(self):
        """Warn at once about a condition on the line's own field.

        Requirements: FR-FVF-0017
        """
        if self.field_name and self.field_name == self.line_id.field_name:
            self.field_name = False
            return {'warning': {
                'title': _("Not allowed"),
                'message': _("A field cannot be shown or hidden based on its own value."),
            }}
