from odoo import api, fields, models


class PerfAuditCheck(models.Model):
    """One check the audit can run, shipped as data and switched on or off.

    Only system administrators reach it (security/ir.access.csv).

    Requirements: FR-OPA-0031, FR-OPA-0033
    """

    _name = 'perf.audit.check'
    _description = 'Performance audit check'
    _order = 'sequence, id'

    code = fields.Char(required=True)
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
    # readonly=False: a computed field without inverse is read-only, so the web client would drop the edits
    shared_threshold_ids = fields.Many2many(
        'perf.audit.threshold', string='Shared thresholds', compute='_compute_shared_threshold_ids', readonly=False)

    _SHARED_THRESHOLD_CHECKS = {
        'big_table': {'A1', 'A2', 'B1', 'B2', 'B3', 'B4', 'B5'},
        'weak_evidence': {'A1', 'A3'},
    }  # the limits shared by every check; query_time_limit applies to all checks

    @api.depends('code')
    def _compute_shared_threshold_ids(self):
        """List the global thresholds the check uses, to edit them where they matter.

        Requirements: FR-OPA-0031
        """
        shared = self.env['perf.audit.threshold'].search([('check_id', '=', False)])
        for check in self:
            check.shared_threshold_ids = shared.filtered(
                lambda threshold: check.code in self._SHARED_THRESHOLD_CHECKS.get(threshold.key, {check.code}))

    _code_unique = models.Constraint('UNIQUE(code)', 'A check code must be unique.')
