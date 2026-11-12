from odoo import Command, _, api, fields, models
from odoo.exceptions import ValidationError


class FvfLayoutLine(models.Model):
    _name = 'fvf.layout.line'
    _description = 'Form layout field line'
    _order = 'sequence, id'

    _field_unique_per_layout = models.UniqueIndex('(layout_id, field_name)')

    layout_id = fields.Many2one(
        'fvf.layout', string='Layout', required=True, ondelete='cascade', index=True)
    field_name = fields.Char(string='Field name', required=True)
    field_id = fields.Many2one(
        'ir.model.fields', string='Field record', ondelete='set null',
        compute='_compute_field_id', store=True, readonly=False)
    field_label = fields.Char(string='Field', compute='_compute_field_id', store=True, readonly=False)
    section_id = fields.Many2one(
        'fvf.layout.section', string='Section', ondelete='set null',
        domain="[('layout_id', '=', layout_id)]")
    sequence = fields.Integer(default=10)
    hidden_group_ids = fields.Many2many(
        'res.groups', 'fvf_layout_line_hidden_group_rel', 'line_id', 'group_id',
        string='Hidden from')
    condition_ids = fields.One2many('fvf.layout.condition', 'line_id', string='Show only when')
    is_required = fields.Boolean(compute='_compute_is_required_and_missing')
    missing = fields.Boolean(compute='_compute_is_required_and_missing')
    condition_summary = fields.Char(compute='_compute_condition_summary')

    @api.depends('field_name', 'layout_id.model')
    def _compute_field_id(self) -> None:
        for line in self:
            field = self.env['ir.model.fields']._get(line.layout_id.model, line.field_name) \
                if line.layout_id.model and line.field_name else self.env['ir.model.fields']
            line.field_id = field
            line.field_label = field.field_description or line.field_name

    @api.depends('field_name', 'layout_id.model', 'layout_id.view_id')
    def _compute_is_required_and_missing(self) -> None:
        """Requirements: BR-FVF-0004 (a field is required when the model or any occurrence in the form says so)"""
        for layout, lines in self.grouped('layout_id').items():
            occurrences = layout._fvf_readable_form_fields()
            model_fields = self.env[layout.model]._fields if layout.model else {}
            for line in lines:
                name = line.field_name
                line.missing = name not in model_fields or name not in occurrences
                model_required = name in model_fields and model_fields[name].required
                form_required = any(
                    attribute not in (None, '', '0', 'False', 'false')
                    for attribute in occurrences.get(name, []))
                line.is_required = bool(model_required or form_required)

    @api.depends('condition_ids.field_name', 'condition_ids.operator', 'condition_ids.value',
                 'condition_ids.value_res_id')
    def _compute_condition_summary(self) -> None:
        for line in self:
            line.condition_summary = _(" and ").join(
                condition._fvf_describe() for condition in line.condition_ids)

    @api.constrains('hidden_group_ids', 'condition_ids', 'field_name')
    def _check_required_field_not_hidden(self) -> None:
        """A required field is neither hidden from groups nor given a condition.

        Requirements: FR-FVF-0016, BR-FVF-0004
        """
        for line in self:
            if line.is_required and (line.hidden_group_ids or line.condition_ids):
                raise ValidationError(_("A required field cannot be hidden."))

    @api.constrains('section_id', 'layout_id')
    def _check_section_in_same_layout(self) -> None:
        """Requirements: FR-FVF-0006"""
        for line in self:
            if line.section_id and line.section_id.layout_id != line.layout_id:
                raise ValidationError(_("The section belongs to another layout."))

    @api.onchange('hidden_group_ids')
    def _onchange_hidden_group_ids(self) -> dict | None:
        """Warn at once and put the groups back when the field is required.

        Requirements: FR-FVF-0016
        """
        if self.is_required and self.hidden_group_ids:
            self.hidden_group_ids = False
            return {'warning': {'title': _("Not allowed"), 'message': _("A required field cannot be hidden.")}}
        return None

    @api.onchange('condition_ids')
    def _onchange_condition_ids(self) -> dict | None:
        """Warn at once and drop the conditions when the field is required.

        Requirements: FR-FVF-0016
        """
        if self.is_required and self.condition_ids:
            self.condition_ids = [Command.clear()]
            return {'warning': {'title': _("Not allowed"), 'message': _("A required field cannot be hidden.")}}
        return None
