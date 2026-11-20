from odoo import api, fields, models

from ..services import performance


class ProcurementSupplierMetric(models.Model):
    """One metric result of a scorecard with its formula, missing reason and evidence.

    Requirements: FR-SPM-0084
    """
    _name = 'procurement.supplier.metric'
    _description = 'Supplier Scorecard Metric'
    _order = 'scorecard_id, id'

    scorecard_id = fields.Many2one('procurement.supplier.scorecard', required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one(related='scorecard_id.company_id', store=True, index=True)
    metric = fields.Selection(selection=lambda self: self.env[
        'procurement.scorecard.template.metric']._fields['metric']._description_selection(self.env), required=True)
    weight = fields.Float()
    value = fields.Float()
    score = fields.Float()
    numerator = fields.Float()
    denominator = fields.Float()
    has_data = fields.Boolean()
    missing_reason = fields.Char()
    formula = fields.Char(compute='_compute_formula')
    trend = fields.Char(compute='_compute_trend', string="Trend")
    evidence_model = fields.Char()
    evidence_ids = fields.Json()

    @api.depends('metric')
    def _compute_formula(self):
        """Requirements: FR-SPM-0084"""
        for row in self:
            text = performance.FORMULAS.get(row.metric)
            row.formula = self.env._(text) if text else False

    @api.depends('metric', 'scorecard_id.period_start')
    def _compute_trend(self):
        """The values of the earlier periods, oldest first; no data periods show a dash.

        Requirements: FR-SPM-0084
        """
        for row in self:
            row.trend = " > ".join(
                "%.0f" % point['value'] if point['has_data'] else "-"
                for point in row.scorecard_id.spm_trend(row.metric)) if row.scorecard_id.id else False

    def action_spm_open_evidence(self):
        """Open the records behind the value.

        Requirements: FR-SPM-0084
        """
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window', 'name': self.env._("Evidence"),
            'res_model': self.evidence_model, 'view_mode': 'list,form',
            'domain': [('id', 'in', self.evidence_ids or [])],
        } if self.evidence_model else {'type': 'ir.actions.act_window_close'}
