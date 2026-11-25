from odoo import fields, models


class ProcurementSourcingNegotiation(models.Model):
    """A request for a revised quotation: the note to the supplier, an internal note, the new deadline.

    Requirements: FR-SPM-0036
    Rows are written by the revision request of the response only; the internal note is
    readable by internal users and never reaches the portal.
    """
    _name = 'procurement.sourcing.negotiation'
    _description = 'Sourcing Negotiation'
    _order = 'date desc, id desc'

    event_id = fields.Many2one('procurement.sourcing.event', required=True, ondelete='cascade', index=True)
    response_id = fields.Many2one('procurement.sourcing.response', required=True, ondelete='cascade', index=True)
    partner_id = fields.Many2one(related='response_id.partner_id', string="Supplier")
    note_to_supplier = fields.Text(required=True)
    internal_note = fields.Text(groups='base.group_user')
    new_deadline = fields.Datetime(required=True)
    user_id = fields.Many2one('res.users', string="Requested by", ondelete='set null')
    date = fields.Datetime(default=fields.Datetime.now, required=True)
    company_id = fields.Many2one(related='event_id.company_id', store=True, index=True)
