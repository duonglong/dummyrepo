from odoo import fields, models


class ProcurementSourcingTemplate(models.Model):
    """Reusable criteria, weights and settings so similar purchases are judged the same way.

    Requirements: FR-SPM-0031
    """
    _name = 'procurement.sourcing.template'
    _description = 'Sourcing Template'
    _order = 'name'

    name = fields.Char(required=True)
    criterion_ids = fields.One2many('procurement.sourcing.criterion', 'template_id', string="Criteria", copy=True)
    inform_unsuccessful = fields.Boolean(string="Inform unsuccessful suppliers")
    company_id = fields.Many2one('res.company', help="Empty: shared by every company.")
