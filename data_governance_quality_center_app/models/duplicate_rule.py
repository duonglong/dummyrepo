import ast

from odoo import api, fields, models
from odoo.exceptions import ValidationError
from odoo.fields import Domain

from ..const import DUPLICATE_MAX_THRESHOLD, DUPLICATE_MIN_THRESHOLD


class DataQualityDuplicateRule(models.Model):
    """What makes two records of a model likely the same.

    Requirements: FR-DGQ-0038
    """
    _name = 'data.quality.duplicate.rule'
    _description = 'Data Quality Duplicate Rule'
    _order = 'name, id'

    name = fields.Char(required=True)
    active = fields.Boolean(default=True)
    model_id = fields.Many2one(
        'ir.model', string='Model', required=True, ondelete='cascade', index=True)
    model_name = fields.Char(related='model_id.model', string='Technical Name', store=True)
    domain = fields.Char(
        string='Filter',
        help="Only the records matching this filter are compared, for example contacts of one "
             "country. Leave empty to compare every active record.")
    threshold = fields.Integer(
        default=85, required=True,
        help="The similarity, from 50 to 100, from which two records are shown as likely duplicates. "
             "With fuzzy matching a low value gives more false matches.")
    company_id = fields.Many2one(
        'res.company', string='Company', ondelete='set null',
        help="Compare only the records of this company. Leave empty to check every company, "
             "each one separately: records of two companies are never grouped.")
    field_line_ids = fields.One2many(
        'data.quality.duplicate.rule.field', 'rule_id', string='Matching Fields', copy=True)
    check_ids = fields.One2many('data.quality.duplicate.check', 'rule_id', string='Checks')
    group_ids = fields.One2many('data.quality.duplicate.group', 'rule_id', string='Groups')

    @api.model_create_multi
    def create(self, vals_list):
        rules = super().create(vals_list)
        rules._check_field_lines()
        return rules

    def write(self, vals):
        if 'field_line_ids' in vals:
            # the lines are deleted before new ones are created: the rule checks them afterwards
            self = self.with_context(dgq_rule_checks_lines=True)
        return super().write(vals)

    @api.constrains('field_line_ids')
    def _check_field_lines(self):
        """A rule needs at least one matching field.

        Requirements: FR-DGQ-0038
        """
        for rule in self:
            if not rule.field_line_ids:
                raise ValidationError(self.env._("A duplicate rule needs at least one matching field."))

    @api.constrains('threshold')
    def _check_threshold(self):
        """The threshold is a similarity from 50 to 100.

        Requirements: FR-DGQ-0038
        """
        for rule in self:
            if not DUPLICATE_MIN_THRESHOLD <= rule.threshold <= DUPLICATE_MAX_THRESHOLD:
                raise ValidationError(self.env._(
                    "The threshold must be between %(min)s and %(max)s.",
                    min=DUPLICATE_MIN_THRESHOLD, max=DUPLICATE_MAX_THRESHOLD))

    @api.constrains('model_id')
    def _check_model_is_supported(self):
        """Only a supported or restricted model can be checked for duplicates.

        Requirements: FR-DGQ-0038, FR-DGQ-0075
        """
        for rule in self:
            status = self.env['data.quality.model.config']._get_status(rule.model_name)
            if status not in ('supported', 'restricted'):
                raise ValidationError(self.env._(
                    "%s is not a model that can be checked for duplicates.", rule.model_id.name))

    @api.constrains('domain', 'model_id')
    def _check_domain(self):
        """The filter must be a domain that the model can search with.

        Requirements: FR-DGQ-0038
        """
        for rule in self:
            if rule.domain:
                try:
                    # sudo: only parses the filter against the model, no record is read
                    self.env[rule.model_name].sudo()._search(rule._get_domain_filter(), limit=1)
                except (ValueError, SyntaxError, KeyError, TypeError) as error:
                    raise ValidationError(self.env._(
                        "The filter of the rule is not valid: %s", error)) from error

    def _get_domain_filter(self):
        """Return the rule's filter as a domain (empty list when none)."""
        self.ensure_one()
        return Domain(ast.literal_eval(self.domain)) if self.domain else Domain.TRUE

    def _get_match_lines(self):
        """Return the matching fields as ``[(field name, method, weight)]``."""
        self.ensure_one()
        return [
            (line.field_id.name, line.method, line.weight)
            for line in self.field_line_ids.sorted('sequence')
        ]

    def action_run_check(self):
        """Start a duplicate check of this rule in the background."""
        checks = self.env['data.quality.duplicate.check'].create([{'rule_id': rule.id} for rule in self])
        checks.action_start()
        return True
