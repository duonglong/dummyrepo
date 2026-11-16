from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.fields import Domain

from ..const import DUPLICATE_MAX_THRESHOLD, DUPLICATE_MIN_THRESHOLD
from ..services.correction import parse_domain


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
        """The filter is checked when it is saved."""
        for rule in self.filtered('domain'):
            rule._dgq_check_domain()

    def _dgq_check_domain(self):
        """Return the filter as an optimised domain, refusing one that cannot be searched or that
        reaches a field a scan may not read.

        A check searches the filter in sudo, so a filter on a sensitive field would show its
        value. The marks change after the filter was saved: every check reads them again.

        Requirements: BR-DGQ-0009, BR-DGQ-0010, FR-DGQ-0038, FR-DGQ-0075
        """
        self.ensure_one()
        rules = self.env['data.quality.rule']
        model = self.env[self.model_name]
        try:
            domain = parse_domain(model, self.domain)
        except (ValueError, SyntaxError, TypeError, KeyError, AttributeError, UserError) as error:
            raise ValidationError(self.env._(
                "The filter of the rule is not valid: %s", error)) from error
        marked = set(rules._dgq_marked_models().mapped('model'))
        blocked = sorted(rules._dgq_blocked_fields(model, domain, {}, marked))
        if blocked:
            raise ValidationError(self.env._(
                "The filter of the rule uses fields that are not stored, are excluded or "
                "sensitive, or lead to a model not marked supported or restricted: %s. "
                "Compare a link with record ids.", ', '.join(blocked)))
        return domain

    def _get_domain_filter(self):
        """Return the rule's filter as a checked domain (empty list when none)."""
        self.ensure_one()
        return self._dgq_check_domain() if self.domain else Domain.TRUE

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
