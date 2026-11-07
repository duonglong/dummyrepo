from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class PerfAuditThreshold(models.Model):
    """One tunable limit of a check, or a global limit of the audit.

    Starting values are shipped in data/perf_audit_threshold_data.xml.

    Requirements: BR-OPA-0006, FR-OPA-0031, FR-OPA-0033
    """

    _name = 'perf.audit.threshold'
    _description = 'Performance audit threshold'
    _order = 'check_id, key'

    check_id = fields.Many2one('perf.audit.check', ondelete='cascade', index=True)
    key = fields.Char(required=True)
    name = fields.Char(required=True, translate=True)
    unit = fields.Selection([
        ('rows', 'Rows'), ('seconds', 'Seconds'), ('minutes', 'Minutes'),
        ('hours', 'Hours'), ('days', 'Days'), ('percent', 'Percent'),
        ('megabytes', 'Megabytes'), ('count', 'Count'),
    ], required=True)
    value = fields.Float(required=True)
    default_value = fields.Float(required=True, readonly=True)

    _key_unique = models.Constraint(
        'UNIQUE NULLS NOT DISTINCT (check_id, key)',
        'A threshold key must be unique per check.')
    _value_positive = models.Constraint(
        'CHECK(value > 0)', 'A threshold must be a positive number.')

    @api.model_create_multi
    def create(self, vals_list):
        """Refuse a non-positive value before the database constraint does.

        Requirements: FR-OPA-0031
        """
        if any(vals.get('value', 1) <= 0 for vals in vals_list):
            raise ValidationError(_('A threshold must be a positive number.'))
        return super().create(vals_list)

    @api.constrains('value')
    def _check_value_positive(self):
        """Refuse a threshold that is not a positive number.

        Requirements: FR-OPA-0031
        """
        if self.filtered(lambda threshold: threshold.value <= 0):
            raise ValidationError(_('A threshold must be a positive number.'))
