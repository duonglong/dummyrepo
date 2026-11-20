from odoo import api, fields, models
from odoo.exceptions import ValidationError

from ..services import performance


class ProcurementScorecardTemplate(models.Model):
    """Metrics, weights, score ranges and evaluation period for suppliers or categories.

    Requirements: FR-SPM-0082, BR-SPM-0013
    A template whose weights total 0 is refused on every path that changes a weight, adds or
    removes a metric row.
    """
    _name = 'procurement.scorecard.template'
    _inherit = ['procurement.audit.mixin']
    _description = 'Scorecard Template'
    _order = 'name, id'

    name = fields.Char(required=True)
    period = fields.Selection([('month', 'Month'), ('quarter', 'Quarter'), ('year', 'Year')],
                              required=True, default='quarter', string="Evaluation period")
    metric_ids = fields.One2many('procurement.scorecard.template.metric', 'template_id', string="Metrics", copy=True)
    good_from = fields.Float(string="Good from", default=85.0)
    acceptable_from = fields.Float(string="Acceptable from", default=60.0)
    review_threshold = fields.Float(
        string="Review below", help="A score below this value asks for a review; 0 never asks.")
    partner_ids = fields.Many2many('res.partner', string="Suppliers")
    product_category_ids = fields.Many2many('product.category', string="Product categories")
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company,
                                 ondelete='cascade', index=True)
    active = fields.Boolean(default=True)

    _spm_audited_fields = ('name', 'period', 'good_from', 'acceptable_from', 'review_threshold', 'active')

    @api.constrains('good_from', 'acceptable_from')
    def _check_ranges(self):
        """Requirements: FR-SPM-0082"""
        for template in self:
            if not 0 <= template.acceptable_from <= template.good_from <= performance.SCORE_MAX:
                raise ValidationError(self.env._(
                    "The limits must satisfy 0 <= acceptable <= good <= %s.", performance.SCORE_MAX))

    def _check_weight_total(self):
        """Refuse a template whose weights total 0.

        Requirements: FR-SPM-0082
        """
        for template in self:
            if sum(template.metric_ids.mapped('weight')) <= 0:
                raise ValidationError(self.env._(
                    "The weights of the template %s total 0: give at least one metric a weight.", template.name))

    @api.model_create_multi
    def create(self, vals_list):
        """Check the weights of the stored result, whether or not metric rows were passed.

        Requirements: FR-SPM-0082
        """
        templates = super(ProcurementScorecardTemplate, self.with_context(spm_weights_deferred=True)).create(vals_list)
        templates = templates.with_context(spm_weights_deferred=False)
        templates._check_weight_total()
        return templates

    def write(self, vals):
        """Check the weights once the whole change, metric row commands included, is stored.

        Requirements: FR-SPM-0082
        """
        if 'metric_ids' not in vals:
            return super().write(vals)
        result = super(ProcurementScorecardTemplate, self.with_context(spm_weights_deferred=True)).write(vals)
        self._check_weight_total()
        return result

    def spm_range(self, score):
        """Return the range of a score by this template's limits.

        Requirements: FR-SPM-0082
        """
        self.ensure_one()
        return performance.range_of(score, self.good_from, self.acceptable_from)
