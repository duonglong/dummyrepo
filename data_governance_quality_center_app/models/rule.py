import ast
import re
from datetime import date, datetime

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.fields import Domain

# What a rule checks: a change of one of these raises the version (FR-DGQ-0011).
# Name, description, advice, sequence and the active switch are not part of the definition.
DEFINITION_FIELDS = (
    'model_id', 'rule_type', 'field_ids', 'severity', 'company_ids', 'pattern_preset', 'pattern',
    'has_min', 'has_max', 'min_value', 'max_value', 'date_condition', 'date_days', 'date_from',
    'date_to', 'applies_domain', 'condition_domain', 'compare_field_id', 'compare_operator',
    'validator_key',
)
RULE_TYPES_WITHOUT_FIELD = ('condition', 'custom')
FIELD_TYPES_OF_RULE_TYPE = {
    'pattern': ('char', 'text'),
    'range': ('integer', 'float', 'monetary'),
    'date': ('date', 'datetime'),
    'link': ('many2one',),
}
COMPARABLE_FIELD_TYPES = (
    ('integer', 'float', 'monetary'), ('date', 'datetime'), ('char', 'text', 'selection'),
    ('many2one',), ('boolean',),
)


class DataQualityRule(models.Model):
    """A written quality policy: which model, which fields, what check, how serious.

    Requirements: FR-DGQ-0001, FR-DGQ-0010, FR-DGQ-0011, FR-DGQ-0014
    """
    _name = 'data.quality.rule'
    _description = 'Data Quality Rule'
    _order = 'sequence, id'

    name = fields.Char(string='Name', required=True, translate=True)
    description = fields.Text(string='Description')
    active = fields.Boolean(
        string='Active', default=True,
        help="A switched-off rule is left out of every scan and score; its issues and their "
             "history are kept. Switch it on again to include it from the next scan.")
    sequence = fields.Integer(string='Sequence', default=10)
    company_ids = fields.Many2many(
        'res.company', string='Companies',
        help="The rule checks only records of these companies. Leave empty to check the records "
             "of every company a scan covers.")

    model_id = fields.Many2one(
        'ir.model', string='Model', required=True, ondelete='cascade', index=True,
        domain=lambda self: self._dgq_model_domain(),
        help="The model whose records are checked. Only models marked supported or restricted "
             "in the settings are offered.")
    model_name = fields.Char(related='model_id.model', string='Technical Name', store=True, index=True)
    rule_type = fields.Selection([
        ('required', 'Required field'),
        ('pattern', 'Pattern'),
        ('range', 'Number range'),
        ('date', 'Date'),
        ('link', 'Link integrity'),
        ('condition', 'Condition'),
        ('cross_field', 'Cross-field'),
        ('custom', 'Custom validator'),
    ], string='Check Type', required=True)
    field_ids = fields.Many2many(
        'ir.model.fields', string='Fields to Check',
        domain="[('model_id', '=', model_id), ('store', '=', True)]",
        help="The fields the check reads. Every check type needs at least one, except "
             "condition and custom validator.")
    severity = fields.Selection([
        ('info', 'Information'),
        ('warning', 'Warning'),
        ('critical', 'Critical'),
    ], string='Severity', required=True, default='warning')

    pattern_preset = fields.Selection([
        ('email', 'Email address'),
        ('phone', 'Phone number'),
        ('url', 'Web address'),
    ], string='Pattern Preset', help="A ready-made pattern; leave empty to type your own pattern.")
    pattern = fields.Char(
        string='Pattern',
        help="A regular expression the whole value must match, for example [A-Z]{2}[0-9]{9}.")
    has_min = fields.Boolean(string='Has Minimum')
    has_max = fields.Boolean(string='Has Maximum')
    min_value = fields.Float(string='Minimum', digits=(16, 4))
    max_value = fields.Float(string='Maximum', digits=(16, 4))
    date_condition = fields.Selection([
        ('not_future', 'Not in the future'),
        ('not_past', 'Not in the past'),
        ('not_older', 'Not older than a number of days'),
        ('not_ahead', 'Not more than a number of days ahead'),
        ('between', 'Between two dates'),
    ], string='Date Condition')
    date_days = fields.Integer(
        string='Days', help="The number of days of the date condition, for example 365.")
    date_from = fields.Date(string='From')
    date_to = fields.Date(string='To')
    applies_domain = fields.Char(
        string='Applies To',
        help="Only records matching this filter are checked, for example "
             "[('is_company', '=', True)]. Leave empty to check every record.")
    condition_domain = fields.Char(
        string='Must Match',
        help="A record of the 'Applies To' filter passes when it matches this filter, for "
             "example [('vat', '!=', False)].")
    compare_field_id = fields.Many2one(
        'ir.model.fields', string='Compare With', ondelete='set null',
        domain="[('model_id', '=', model_id), ('store', '=', True)]")
    compare_operator = fields.Selection([
        ('=', 'Equal to'),
        ('!=', 'Not equal to'),
        ('>', 'Greater than'),
        ('>=', 'Greater than or equal to'),
        ('<', 'Lower than'),
        ('<=', 'Lower than or equal to'),
    ], string='Operator')
    validator_key = fields.Selection(
        selection=lambda self: self.env['data.quality.registry']._dgq_selection('validators'),
        string='Validator',
        help="A validator provided by an installed module.")
    advice = fields.Text(
        string='Advice', translate=True,
        help="How to put a failing record right; shown with each issue of the rule.")

    version = fields.Integer(string='Version', default=1, readonly=True, copy=False)
    version_ids = fields.One2many(
        'data.quality.rule.version', 'rule_id', string='Versions', readonly=True, copy=False)

    @api.model
    def _dgq_model_domain(self):
        """Domain offering only the models marked supported or restricted."""
        # sudo: the marks are an administrator setting that every rule author needs to see
        marks = self.env['data.quality.model.config'].sudo().search(
            [('status', 'in', ('supported', 'restricted'))])
        return [('id', 'in', marks.model_id.ids)]

    @api.model
    def _dgq_serialise(self, value):
        """A field value as JSON-safe data for a kept version."""
        if isinstance(value, models.BaseModel):
            return sorted(value.ids)
        if isinstance(value, (date, datetime)):
            return str(value)
        return value

    def _dgq_definition(self):
        """What the rule checks now, as JSON-safe data keyed by field name."""
        self.ensure_one()
        return {name: self._dgq_serialise(self[name]) for name in DEFINITION_FIELDS}

    # ---- constraints -----------------------------------------------------

    @api.constrains('model_id')
    def _check_model_is_marked(self):
        """Only models marked supported or restricted can be the model of a rule.

        Requirements: FR-DGQ-0001
        """
        allowed = set(self._dgq_model_domain()[0][2])
        for rule in self:
            if rule.model_id.id not in allowed:
                raise ValidationError(self.env._(
                    "%s is not marked as supported in the data quality settings and cannot be "
                    "checked by a rule. A data quality administrator can mark it in Data Quality > "
                    "Configuration > Models.", rule.model_id.name))

    @api.constrains('field_ids', 'compare_field_id', 'model_id', 'rule_type')
    def _check_fields(self):
        """Fields belong to the model, are readable by scans and fit the check type.

        Requirements: FR-DGQ-0001
        """
        usable_names = self.env['data.quality.field.config']
        for rule in self:
            if not rule.field_ids and rule.rule_type not in RULE_TYPES_WITHOUT_FIELD:
                raise ValidationError(self.env._(
                    "The rule %s needs at least one field to check.", rule.name))
            usable = usable_names._get_usable_field_names(rule.model_name)
            for field in rule.field_ids | rule.compare_field_id:
                if field.model_id != rule.model_id or not field.store or field.name not in usable:
                    raise ValidationError(self.env._(
                        "The field %(field)s cannot be checked on %(model)s: it is not a stored "
                        "field of the model, or it is excluded or sensitive. A data quality "
                        "administrator can mark a field as allowed in Data Quality > "
                        "Configuration > Fields.",
                        field=field.display_name, model=rule.model_id.name))
            allowed_types = FIELD_TYPES_OF_RULE_TYPE.get(rule.rule_type)
            for field in rule.field_ids:
                if allowed_types and field.ttype not in allowed_types:
                    raise ValidationError(self.env._(
                        "The field %(field)s (%(type)s) does not fit the check type %(check)s.",
                        field=field.display_name, type=field.ttype,
                        check=dict(self._fields['rule_type']._description_selection(self.env))[rule.rule_type]))

    @api.constrains('rule_type', 'pattern', 'pattern_preset')
    def _check_pattern(self):
        """A pattern rule has a pattern that compiles.

        Requirements: FR-DGQ-0003
        """
        for rule in self.filtered(lambda r: r.rule_type == 'pattern'):
            if not rule.pattern and not rule.pattern_preset:
                raise ValidationError(self.env._(
                    "The pattern rule %s needs a pattern or a preset.", rule.name))
            if rule.pattern:
                try:
                    re.compile(rule.pattern)
                except re.error as error:
                    raise ValidationError(self.env._(
                        "The pattern %(pattern)s is not a valid regular expression: %(error)s",
                        pattern=rule.pattern, error=error)) from error

    @api.constrains('rule_type', 'has_min', 'has_max', 'min_value', 'max_value')
    def _check_range(self):
        """A range rule has at least one bound and a minimum not above the maximum.

        Requirements: FR-DGQ-0004
        """
        for rule in self.filtered(lambda r: r.rule_type == 'range'):
            if not rule.has_min and not rule.has_max:
                raise ValidationError(self.env._(
                    "The range rule %s needs a minimum or a maximum.", rule.name))
            if rule.has_min and rule.has_max and rule.min_value > rule.max_value:
                raise ValidationError(self.env._(
                    "The minimum of the rule %s cannot be above its maximum.", rule.name))

    @api.constrains('rule_type', 'date_condition', 'date_days', 'date_from', 'date_to')
    def _check_date(self):
        """A date rule has its condition and the values the condition needs.

        Requirements: FR-DGQ-0005
        """
        for rule in self.filtered(lambda r: r.rule_type == 'date'):
            condition = rule.date_condition
            if not condition:
                raise ValidationError(self.env._(
                    "The date rule %s needs a date condition.", rule.name))
            if condition in ('not_older', 'not_ahead') and rule.date_days <= 0:
                raise ValidationError(self.env._(
                    "The date rule %s needs a number of days above 0.", rule.name))
            if condition == 'between' and (
                    not rule.date_from or not rule.date_to or rule.date_from > rule.date_to):
                raise ValidationError(self.env._(
                    "The date rule %s needs two dates, the first not after the second.", rule.name))

    @api.constrains('rule_type', 'applies_domain', 'condition_domain', 'model_id')
    def _check_domains(self):
        """Filters are plain domain text that resolves on the model; they are never run as code.

        Requirements: FR-DGQ-0007, BR-DGQ-0009
        """
        for rule in self:
            for label, text in (('Applies To', rule.applies_domain), ('Must Match', rule.condition_domain)):
                if text:
                    rule._dgq_validate_domain(label, text)
            if rule.rule_type == 'condition' and not rule.condition_domain:
                raise ValidationError(self.env._(
                    "The condition rule %s needs a 'Must Match' filter.", rule.name))

    def _dgq_validate_domain(self, label, text):
        """Raise a ValidationError when ``text`` is not a domain that resolves on the model."""
        self.ensure_one()
        try:
            domain = Domain(ast.literal_eval(text))
            domain.validate(self.env[self.model_name])
            used = {condition.field_expr.split('.')[0] for condition in domain.iter_conditions()}
        except (ValueError, SyntaxError, TypeError, KeyError, AttributeError, UserError) as error:
            raise ValidationError(self.env._(
                "The filter %(label)s of the rule %(rule)s is not a valid domain on %(model)s: %(error)s",
                label=label, rule=self.name, model=self.model_id.name, error=error)) from error
        usable = self.env['data.quality.field.config']._get_usable_field_names(self.model_name)
        model_fields = self.env[self.model_name]._fields
        # a non-stored field reads no stored secret of its own, so only stored fields are held to the marks
        blocked = sorted(
            name for name in used if model_fields[name].store and name not in usable)
        if blocked:
            raise ValidationError(self.env._(
                "The filter %(label)s of the rule %(rule)s uses fields that are excluded or "
                "sensitive: %(fields)s. A data quality administrator can mark a field as allowed in "
                "Data Quality > Configuration > Fields.",
                label=label, rule=self.name, fields=', '.join(blocked)))

    @api.constrains('rule_type', 'field_ids', 'compare_field_id', 'compare_operator')
    def _check_cross_field(self):
        """A cross-field rule compares two fields of kinds that can be compared.

        Requirements: FR-DGQ-0008
        """
        for rule in self.filtered(lambda r: r.rule_type == 'cross_field'):
            if not rule.compare_field_id or not rule.compare_operator:
                raise ValidationError(self.env._(
                    "The cross-field rule %s needs a field to compare with and an operator.", rule.name))
            for field in rule.field_ids:
                if not any(
                        field.ttype in kinds and rule.compare_field_id.ttype in kinds
                        for kinds in COMPARABLE_FIELD_TYPES):
                    raise ValidationError(self.env._(
                        "The fields %(first)s and %(second)s cannot be compared with each other.",
                        first=field.display_name, second=rule.compare_field_id.display_name))

    @api.constrains('rule_type', 'validator_key')
    def _check_validator(self):
        """A custom rule names a validator that an installed module registers.

        Requirements: FR-DGQ-0009, BR-DGQ-0009
        """
        registry = self.env['data.quality.registry']
        for rule in self.filtered(lambda r: r.rule_type == 'custom'):
            if not rule.validator_key or not registry._dgq_find('validators', rule.validator_key):
                raise ValidationError(self.env._(
                    "The custom rule %s needs a validator provided by an installed module.", rule.name))

    # ---- versions --------------------------------------------------------

    def write(self, vals):
        """Keep the previous definition as a version when the definition changes.

        A change of the model, check type, fields, settings, filters, severity or companies raises
        the version by one; the name, description, advice and the active switch do not.

        Requirements: FR-DGQ-0011
        """
        if 'version' in vals:
            raise UserError(self.env._(
                "The version of a rule is raised by changing its definition; it cannot be set by hand."))
        if not any(name in vals for name in DEFINITION_FIELDS):
            return super().write(vals)
        before = {rule.id: rule._dgq_definition() for rule in self}
        result = super().write(vals)
        for rule in self:
            after = rule._dgq_definition()
            changed = {
                name: [before[rule.id][name], value]
                for name, value in after.items() if value != before[rule.id][name]}
            if changed:
                # sudo: versions are read-only for every user; only the rule writes them
                self.env['data.quality.rule.version'].sudo().create({
                    'rule_id': rule.id,
                    'version': rule.version,
                    'definition': before[rule.id],
                    'changed_values': changed,
                    'user_id': self.env.user.id,
                })
                super(DataQualityRule, rule).write({'version': rule.version + 1})
        return result

    def copy_data(self, default=None):
        """A copy is a new rule: named '(copy)', at version 1, with no versions.

        Requirements: FR-DGQ-0011
        """
        vals_list = super().copy_data(default)
        if default and 'name' in default:
            return vals_list
        return [
            dict(vals, name=self.env._("%s (copy)", rule.name))
            for rule, vals in zip(self, vals_list)]
