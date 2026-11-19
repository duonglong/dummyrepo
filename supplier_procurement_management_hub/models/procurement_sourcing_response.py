from odoo import fields, models


class ProcurementSourcingResponse(models.Model):
    """One invited supplier's answer to a sourcing event (created when the event is sent).

    Requirements: FR-SPM-0032
    Built early for T-014 (quotation versions), which adds the versions, the submission and
    the scoring; only the header the sending needs exists here.
    """
    _name = 'procurement.sourcing.response'
    _description = 'Sourcing Response'
    _order = 'id'

    event_id = fields.Many2one('procurement.sourcing.event', required=True, ondelete='cascade', index=True)
    partner_id = fields.Many2one('res.partner', string="Supplier", required=True, ondelete='restrict', index=True)
    state = fields.Selection([('invited', 'Invited'), ('submitted', 'Submitted')],
                             required=True, default='invited')
    company_id = fields.Many2one(related='event_id.company_id', store=True, index=True)

    _event_partner_unique = models.Constraint(
        'UNIQUE(event_id, partner_id)', 'A supplier answers an event once.')
