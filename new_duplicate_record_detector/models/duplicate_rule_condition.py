from odoo import api, fields, models
from odoo.exceptions import ValidationError

MATCHABLE_TYPES = ('char', 'text', 'selection', 'many2one')
PARTIAL_TYPES = ('char', 'text')
BOOKKEEPING_FIELDS = ('id', 'create_uid', 'create_date', 'write_uid', 'write_date')


class DuplicateRuleCondition(models.Model):
    _name = 'duplicate.rule.condition'
    _description = 'Duplicate Rule Condition'
    _order = 'sequence, id'

    rule_id = fields.Many2one(
        'duplicate.rule', required=True, index=True, ondelete='cascade')
    sequence = fields.Integer(default=10)
    field_id = fields.Many2one(
        'ir.model.fields', string='Field', required=True, ondelete='cascade',
        help="The field that must match. Text, choice and single-link fields can be used.")
    field_type = fields.Selection(related='field_id.ttype', string='Field type')
    match_type = fields.Selection(
        [('exact', 'Exact'), ('partial', 'Partial')], required=True, default='exact',
        help="Exact: same value, ignoring capitals and outer spaces. Partial: one value "
             "contains the other (text fields only).")

    @api.constrains('field_id', 'match_type')
    def _check_field_is_matchable(self):
        """Requirements: FR-DRD-0002 (refuse conditions that cannot work)."""
        for condition in self:
            field = condition.field_id
            if (field.model != condition.rule_id.model_name
                    or not self._is_matchable(field)):
                raise ValidationError(self.env._(
                    "The field \"%s\" cannot be used in a rule: only stored text, choice "
                    "and single-link fields of the rule's record type can.",
                    field.field_description))
            if condition.match_type == 'partial' and field.ttype not in PARTIAL_TYPES:
                raise ValidationError(self.env._(
                    "Partial matching works on text fields only; \"%s\" is not a text field.",
                    field.field_description))

    @api.model
    def _is_matchable(self, field):
        """Tell whether an ir.model.fields record may be a matching field."""
        return (
            field.store
            and field.ttype in MATCHABLE_TYPES
            and field.name not in BOOKKEEPING_FIELDS
        )
