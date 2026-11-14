from odoo import fields, models


class DataQualityScoreSnapshot(models.Model):
    """The dated score of a model for a company, overall and per rule, with its weights.

    The dashboard and the trend read these rows, never the scan results, so they stay fast.

    Requirements: FR-DGQ-0031, BR-DGQ-0013
    """
    _name = 'data.quality.score.snapshot'
    _description = 'Data Quality Score Snapshot'
    _order = 'date desc, id desc'

    date = fields.Datetime(required=True, default=fields.Datetime.now, index=True)
    scan_id = fields.Many2one('data.quality.scan', ondelete='set null', index=True)
    company_id = fields.Many2one('res.company', required=True, ondelete='cascade')
    model_id = fields.Many2one('ir.model', required=True, ondelete='cascade')
    rule_id = fields.Many2one('data.quality.rule', ondelete='set null', index=True)
    rule_version = fields.Integer()
    severity = fields.Selection([
        ('info', 'Information'),
        ('warning', 'Warning'),
        ('critical', 'Critical'),
    ])
    applicable_weight = fields.Integer()
    failed_weight = fields.Integer()
    score = fields.Float()
    has_score = fields.Boolean(help="Off when no check applied: there is no score to show, not 100.")
    records_scanned = fields.Integer()
    records_failing = fields.Integer()
    records_error = fields.Integer()
    records_warning = fields.Integer()
    issues_open_info = fields.Integer()
    issues_open_warning = fields.Integer()
    issues_open_critical = fields.Integer()
    weights_used = fields.Json()
    is_illustrative = fields.Boolean()

    def init(self):
        """Indexes of the searches the dashboard makes most (NFR-DGQ-0004)."""
        self.env.cr.execute(
            "CREATE INDEX IF NOT EXISTS dgq_snapshot_company_model_date_idx "
            "ON data_quality_score_snapshot (company_id, model_id, date)")
