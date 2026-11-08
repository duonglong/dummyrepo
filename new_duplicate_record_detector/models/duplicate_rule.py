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

    group_ids = fields.One2many(
        'duplicate.group', 'rule_id', string='Sets found', copy=False)
    open_group_count = fields.Integer(
        compute='_compute_open_group_count', help="Sets of possible duplicates of this rule still to review.")

    matchable_field_ids = fields.Many2many(
        'ir.model.fields', compute='_compute_matchable_field_ids',
        help="Fields of the record type that a condition may use.")

    @api.depends('model_id')
    def _compute_matchable_field_ids(self) -> None:
        """Requirements: FR-DRD-0002 (offer only fields a condition may use)."""
        candidates = self.env['ir.model.fields'].search(
            [('model_id', 'in', self.model_id.ids)]).filtered(
                self.env['duplicate.rule.condition']._is_matchable)
        for rule in self:
            rule.matchable_field_ids = candidates.filtered(
                lambda f: f.model_id == rule.model_id)

    @api.depends('group_ids.state')
    def _compute_open_group_count(self) -> None:
        """Requirements: FR-DRD-0004 (count the open sets of each rule)."""
        counts = dict(self.env['duplicate.group']._read_group(
            [('rule_id', 'in', self.ids), ('state', '=', 'open')], ['rule_id'], ['__count']))
        for rule in self:
            rule.open_group_count = counts.get(rule, 0)

    @api.model_create_multi
    def create(self, vals_list: list[dict]):
        """Requirements: FR-DRD-0002 (a rule created without conditions is refused too).

        The constraint on condition_ids only runs when the key is sent, so an import or an
        API call that leaves it out would store a rule without condition.
        """
        rules = super().create(vals_list)
        rules._check_has_condition()
        return rules

    def write(self, vals: dict):
        """Requirements: FR-DRD-0002 (check the conditions once, on the final state of a save).

        The one2many write deletes conditions before it creates the new ones; the context key
        tells the condition's unlink not to judge that intermediate state.
        """
        return super(DuplicateRule, self.with_context(drd_rule_write=True)).write(vals)

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
