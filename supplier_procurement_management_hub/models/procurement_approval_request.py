from odoo import api, fields, models


class ProcurementApprovalRequest(models.Model):
    """The approval of one revision of a purchase order, with the figures it was decided on.

    Requirements: FR-SPM-0047, FR-SPM-0052
    Created, decided and cancelled only by services.approval (with elevated rights);
    users read it through the access rows.
    """
    _name = 'procurement.approval.request'
    _description = 'Approval Request'
    _order = 'id desc'

    name = fields.Char(compute='_compute_name')
    order_id = fields.Many2one('purchase.order', required=True, ondelete='cascade', index=True, readonly=True)
    revision = fields.Integer(required=True, readonly=True)
    state = fields.Selection([
        ('pending', 'To approve'), ('approved', 'Approved'), ('rejected', 'Rejected'),
        ('cancelled', 'Cancelled'),
    ], required=True, default='pending', index=True, readonly=True)
    policy_ids = fields.Many2many('procurement.approval.policy', string="Policies", readonly=True)
    step_ids = fields.One2many('procurement.approval.step', 'request_id', string="Steps", readonly=True)
    decision_ids = fields.One2many('procurement.approval.decision', 'request_id', string="Decisions", readonly=True)
    submitted_by_id = fields.Many2one('res.users', string="Submitted by", ondelete='restrict', readonly=True)
    submitted_date = fields.Datetime(readonly=True)
    decided_date = fields.Datetime(readonly=True)
    cancel_reason = fields.Selection([('material_change', 'Material change')], readonly=True)
    snapshot_amount = fields.Monetary(currency_field='snapshot_currency_id', readonly=True)
    snapshot_currency_id = fields.Many2one('res.currency', ondelete='restrict', readonly=True)
    snapshot_partner_id = fields.Many2one('res.partner', string="Vendor", ondelete='restrict', readonly=True)
    snapshot_payment_term_id = fields.Many2one('account.payment.term', ondelete='restrict', readonly=True)
    snapshot_lines = fields.Json(readonly=True)
    company_currency_id = fields.Many2one(related='company_id.currency_id')
    cumulated_amount = fields.Monetary(currency_field='company_currency_id', readonly=True)
    company_id = fields.Many2one(related='order_id.company_id', store=True, index=True)

    @api.depends('order_id.name', 'revision')
    def _compute_name(self):
        for request in self:
            request.name = f"{request.order_id.name or ''} r{request.revision}"
