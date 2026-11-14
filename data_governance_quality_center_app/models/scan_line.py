from odoo import api, fields, models

from ..services import validation


class DataQualityScanLine(models.Model):
    """One failed check of one record in one scan.

    Requirements: FR-DGQ-0015, FR-DGQ-0019
    """
    _name = 'data.quality.scan.line'
    _inherit = ['data.quality.result.mixin']
    _description = 'Data Quality Scan Result'

    scan_id = fields.Many2one('data.quality.scan', required=True, ondelete='cascade', index=True)
    rule_id = fields.Many2one('data.quality.rule', required=True, ondelete='restrict')
    rule_version = fields.Integer()
    res_id = fields.Many2oneReference(string='Record', model_field='res_model', index=True)
    severity = fields.Selection([
        ('info', 'Information'),
        ('warning', 'Warning'),
        ('critical', 'Critical'),
    ])
    explanation_code = fields.Char()
    explanation_params = fields.Json()
    explanation = fields.Text(compute='_compute_explanation')

    @api.depends('explanation_code', 'explanation_params', 'res_model')
    def _compute_explanation(self):
        """Render the stored code in the reader's language."""
        for line in self:
            line.explanation = validation.explain(
                self.env, line.explanation_code, line.explanation_params or {}) or False
