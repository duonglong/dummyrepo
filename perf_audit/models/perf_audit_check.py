from odoo import fields, models


class PerfAuditCheck(models.Model):
    """One check the audit can run, shipped as data and switched on or off.

    Only system administrators reach it (security/ir.access.csv).

    Requirements: FR-OPA-0031, FR-OPA-0033
    """

    _name = 'perf.audit.check'
    _description = 'Performance audit check'
    _order = 'sequence, id'

    code = fields.Char(required=True, index=True)
    name = fields.Char(required=True, translate=True)
    category = fields.Selection(
        [('database', 'Database'), ('odoo', 'Odoo')], required=True)
    severity = fields.Selection([
        ('high', 'High'), ('medium', 'Medium'), ('low', 'Low'),
        ('variable', 'Variable'),
    ])
    sequence = fields.Integer(default=10)
    active_check = fields.Boolean(string='Enabled', default=True)
    threshold_ids = fields.One2many('perf.audit.threshold', 'check_id', string='Thresholds')

    _code_unique = models.Constraint('UNIQUE(code)', 'A check code must be unique.')
