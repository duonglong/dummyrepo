from odoo import api, fields, models


class ProcurementSupplierEvaluationRating(models.Model):
    """One criterion rated from 1 to 5 in an evaluation; it changes only when its evaluation can.

    Requirements: FR-SPM-0085
    """
    _name = 'procurement.supplier.evaluation.rating'
    _description = 'Supplier Evaluation Rating'
    _order = 'evaluation_id, id'

    evaluation_id = fields.Many2one('procurement.supplier.evaluation', required=True, ondelete='cascade', index=True)
    criterion_id = fields.Many2one('procurement.evaluation.criterion', string="Criterion", required=True,
                                   ondelete='restrict')
    company_id = fields.Many2one(related='evaluation_id.company_id', store=True, index=True)
    rating = fields.Selection([('1', '1'), ('2', '2'), ('3', '3'), ('4', '4'), ('5', '5')], required=True)

    _criterion_once = models.Constraint(
        'UNIQUE(evaluation_id, criterion_id)', 'A criterion is rated once per evaluation.')

    @api.model_create_multi
    def create(self, vals_list):
        """Requirements: FR-SPM-0085"""
        ratings = super().create(vals_list)
        ratings.evaluation_id._spm_check_editable()
        return ratings

    def write(self, vals):
        """Requirements: FR-SPM-0085"""
        self.evaluation_id._spm_check_editable()
        return super().write(vals)

    def unlink(self):
        """Requirements: FR-SPM-0085"""
        self.evaluation_id._spm_check_editable()
        return super().unlink()
