from odoo import api, fields, models

from ..services import performance


class ProcurementScorecardTemplateMetric(models.Model):
    """A metric of a template with its weight and review threshold.

    Requirements: FR-SPM-0082, FR-SPM-0079, FR-SPM-0080, FR-SPM-0081
    """
    _name = 'procurement.scorecard.template.metric'
    _description = 'Scorecard Template Metric'
    _order = 'template_id, id'

    template_id = fields.Many2one('procurement.scorecard.template', required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one(related='template_id.company_id', store=True, index=True)
    metric = fields.Selection([
        ('on_time', 'On-time delivery'), ('fulfillment', 'Quantity fulfillment'),
        ('quality', 'Quality acceptance'), ('complaints', 'Complaints'),
        ('responsiveness', 'Responsiveness'), ('price_competitiveness', 'Price competitiveness'),
    ], required=True)
    weight = fields.Float(default=1.0)
    review_threshold = fields.Float(
        string="Review below", help="A metric score below this value asks for a review; 0 never asks.")

    _metric_unique = models.Constraint('UNIQUE(template_id, metric)', 'A template holds a metric once.')
    _weight_not_negative = models.Constraint('CHECK(weight >= 0)', 'The weight cannot be negative.')

    @api.model
    def _spm_metric_computers(self):
        """Return the computer of every metric kind: a function (env, partner, company, start, end).

        Requirements: FR-SPM-0079, FR-SPM-0080, FR-SPM-0081
        Extension point: another module adds a kind with ``selection_add`` and overrides this method.
        """
        return {
            'on_time': performance.on_time, 'fulfillment': performance.fulfillment,
            'quality': performance.quality, 'complaints': performance.complaints,
            'responsiveness': performance.responsiveness,
            'price_competitiveness': performance.price_competitiveness,
        }

    def write(self, vals):
        """Check the weights of the templates after a row changes, unless the template does it once.

        Requirements: FR-SPM-0082
        """
        result = super().write(vals)
        if not self.env.context.get('spm_weights_deferred') and {'weight', 'template_id'} & set(vals):
            self.template_id._check_weight_total()
        return result

    def unlink(self):
        """Refuse a removal that leaves the weights of a saved template at 0.

        Requirements: FR-SPM-0082
        """
        templates = self.template_id
        result = super().unlink()
        if not self.env.context.get('spm_weights_deferred'):
            templates.exists()._check_weight_total()
        return result
