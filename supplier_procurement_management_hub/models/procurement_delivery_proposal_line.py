from odoo import fields, models


class ProcurementDeliveryProposalLine(models.Model):
    """One proposed date and quantity of a split proposal.

    Requirements: FR-SPM-0025
    """
    _name = 'procurement.delivery.proposal.line'
    _description = 'Delivery Proposal Line'
    _order = 'date, id'

    proposal_id = fields.Many2one('procurement.delivery.proposal', string="Proposal", required=True,
                                  ondelete='cascade', index=True)
    company_id = fields.Many2one(related='proposal_id.company_id', store=True, index=True)
    date = fields.Datetime(required=True)
    quantity = fields.Float(digits='Product Unit')

    _quantity_positive = models.Constraint('CHECK(quantity > 0)', 'A proposed quantity must be positive.')
