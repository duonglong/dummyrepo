from odoo import fields, models


class PerfAuditCheckResult(models.Model):
    """What one check did in one run: done, skipped or failed, why, how long, how many findings.

    Requirements: FR-OPA-0001, FR-OPA-0003, FR-OPA-0002
    """

    _name = 'perf.audit.check.result'
    _description = 'Performance audit check result'
    _order = 'run_id, check_id'

    run_id = fields.Many2one('perf.audit.run', required=True, index=True, ondelete='cascade')
    check_id = fields.Many2one('perf.audit.check', required=True, ondelete='restrict')
    code = fields.Char(related='check_id.code', store=True)
    state = fields.Selection(
        [('done', 'Done'), ('skipped', 'Skipped'), ('failed', 'Failed')], required=True)
    reason = fields.Char()
    part_skip_reason = fields.Char()
    duration = fields.Float(help='Seconds')
    finding_count = fields.Integer()
    thresholds_used = fields.Json()
    measures = fields.Json()

    _run_check_unique = models.Constraint(
        'UNIQUE(run_id, check_id)', 'A check has one result per run.')
