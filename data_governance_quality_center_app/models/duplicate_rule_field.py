from odoo import api, fields, models
from odoo.exceptions import ValidationError

from ..const import DUPLICATE_MAX_WEIGHT, DUPLICATE_MIN_WEIGHT

TEXT_FIELD_TYPES = ('char', 'text')
UNMATCHABLE_FIELD_TYPES = ('one2many', 'many2many', 'binary', 'json', 'properties')


class DataQualityDuplicateRuleField(models.Model):
    """One matching field of a duplicate rule, with its method and weight.

    Requirements: FR-DGQ-0038, BR-DGQ-0012
    """
    _name = 'data.quality.duplicate.rule.field'
    _description = 'Data Quality Duplicate Rule Field'
    _order = 'sequence, id'

    rule_id = fields.Many2one(
        'data.quality.duplicate.rule', string='Rule', required=True, ondelete='cascade', index=True)
    sequence = fields.Integer(default=10)
    field_id = fields.Many2one(
        'ir.model.fields', string='Field', required=True, ondelete='cascade',
        domain="[('model_id', '=', parent.model_id), ('store', '=', True)]")
    method = fields.Selection([
        ('exact', 'Exact'),
        ('normalized', 'Normalized'),
        ('fuzzy', 'Fuzzy'),
    ], required=True, default='normalized',
        help="Exact: the values are equal as stored. Normalized: equal when case, accents, spaces and "
             "punctuation are ignored. Fuzzy: the more alike, the higher the score (text fields only).")
    weight = fields.Integer(
        default=DUPLICATE_MIN_WEIGHT, required=True,
        help="How much this field counts in the similarity, from 1 to 10. A field of weight 3 counts "
             "three times as much as one of weight 1.")

    def unlink(self):
        """Refuse to remove the last matching field of a rule that stays.

        A change of the rule's own lines is checked by the rule once all its lines are written,
        so a field can be replaced in one save.

        Requirements: FR-DGQ-0038
        """
        rules = self.rule_id
        result = super().unlink()
        if not self.env.context.get('dgq_rule_checks_lines'):
            rules.exists()._check_field_lines()
        return result

    @api.constrains('weight')
    def _check_weight(self):
        """A weight is a whole number from 1 to 10.

        Requirements: FR-DGQ-0038
        """
        for line in self:
            if not DUPLICATE_MIN_WEIGHT <= line.weight <= DUPLICATE_MAX_WEIGHT:
                raise ValidationError(self.env._(
                    "The weight of %(field)s must be between %(min)s and %(max)s.",
                    field=line.field_id.field_description, min=DUPLICATE_MIN_WEIGHT,
                    max=DUPLICATE_MAX_WEIGHT))

    @api.constrains('field_id', 'method', 'rule_id')
    def _check_field(self):
        """The field belongs to the rule's model, may be read, and fuzzy matching needs a text field.

        Requirements: FR-DGQ-0038, BR-DGQ-0010
        """
        for line in self:
            field = line.field_id
            if field.model_id != line.rule_id.model_id:
                raise ValidationError(self.env._(
                    "%(field)s is not a field of %(model)s.",
                    field=field.field_description, model=line.rule_id.model_id.name))
            usable = self.env['data.quality.field.config']._get_usable_field_names(field.model)
            if not field.store or field.ttype in UNMATCHABLE_FIELD_TYPES or field.name not in usable:
                raise ValidationError(self.env._(
                    "%s cannot be used in a duplicate rule: it is not stored, or is excluded or sensitive.",
                    field.field_description))
            if line.method == 'fuzzy' and field.ttype not in TEXT_FIELD_TYPES:
                raise ValidationError(self.env._(
                    "Fuzzy matching applies to text fields only: %s is not one.", field.field_description))
