from odoo import api, fields, models
from odoo.exceptions import ValidationError

WATCHED_MODELS = ('res.partner', 'product.template')


class DuplicateRule(models.Model):
    _name = 'duplicate.rule'
    _description = 'Duplicate Detection Rule'

    name = fields.Char(required=True)
    model_id = fields.Many2one(
        'ir.model', string='Applies to', required=True, ondelete='cascade',
        domain=[('model', 'in', WATCHED_MODELS)],
        help="Contacts or product templates: the records this rule looks at.")
    model_name = fields.Char(related='model_id.model', store=True, index=True)
    active = fields.Boolean(
        default=True, help="Switch the rule off to keep it without using it.")
    condition_ids = fields.One2many(
        'duplicate.rule.condition', 'rule_id', string='Conditions', copy=True,
        help="Every condition must match for two records to be possible duplicates.")

    matchable_field_ids = fields.Many2many(
        'ir.model.fields', compute='_compute_matchable_field_ids',
        help="Fields of the record type that a condition may use.")

    @api.depends('model_id')
    def _compute_matchable_field_ids(self) -> None:
        """Requirements: FR-DRD-0002 (offer only fields a condition may use)."""
        Field = self.env['ir.model.fields']
        for rule in self:
            candidates = Field.search([('model_id', '=', rule.model_id.id)])
            rule.matchable_field_ids = candidates.filtered(
                self.env['duplicate.rule.condition']._is_matchable)

    @api.model_create_multi
    def create(self, vals_list: list[dict]):
        """Requirements: FR-DRD-0002 (a rule created without conditions is refused too).

        The constraint on condition_ids only runs when the key is sent, so an import or an
        API call that leaves it out would store a rule without condition.
        """
        rules = super().create(vals_list)
        rules._check_has_condition()
        return rules

    @api.constrains('model_id')
    def _check_model(self) -> None:
        """Requirements: FR-DRD-0001 (only contacts or product templates are watched)."""
        for rule in self.filtered(lambda r: r.model_id.model not in WATCHED_MODELS):
            raise ValidationError(self.env._(
                "A rule can only look at contacts or product templates, not at %s.",
                rule.model_id.name))

    @api.constrains('condition_ids')
    def _check_has_condition(self) -> None:
        """Requirements: FR-DRD-0002 (a rule needs at least one condition)."""
        for rule in self.filtered(lambda r: not r.condition_ids):
            raise ValidationError(self.env._(
                "The rule \"%s\" needs at least one condition: without one it would flag "
                "every record as a duplicate of every other.", rule.name))

    @api.constrains('model_id')
    def _check_conditions_fit_model(self) -> None:
        """Requirements: FR-DRD-0002 (conditions keep to the fields of the rule's record type)."""
        self.condition_ids._check_field_is_matchable()
