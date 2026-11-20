from odoo import fields, models


class ProcurementEvaluationCriterion(models.Model):
    """A criterion a qualitative evaluation rates (communication, cooperation, innovation).

    Requirements: FR-SPM-0085
    """
    _name = 'procurement.evaluation.criterion'
    _description = 'Evaluation Criterion'
    _order = 'name, id'

    name = fields.Char(required=True, translate=True)
    active = fields.Boolean(default=True)
