from odoo import api, fields, models
from odoo.exceptions import ValidationError

from ..services import delivery


class ProcurementDeliveryCommitment(models.Model):
    """The delivery promise of one confirmed goods line: three dates, quantities read from receipts.

    Requirements: FR-SPM-0055, FR-SPM-0059, FR-SPM-0061, BR-SPM-0012
    Quantities and dates are computed from Odoo's own receipts; no stock move is ever written here.
    """
    _name = 'procurement.delivery.commitment'
    _description = 'Delivery Commitment'
    _order = 'agreed_date, id'

    order_line_id = fields.Many2one('purchase.order.line', string="Order line", required=True,
                                    ondelete='cascade', index=True, readonly=True)
    order_id = fields.Many2one(related='order_line_id.order_id', store=True, index=True)
    partner_id = fields.Many2one(related='order_line_id.partner_id', store=True, index=True)
    product_id = fields.Many2one(related='order_line_id.product_id', store=True)
    company_id = fields.Many2one(related='order_line_id.company_id', store=True, index=True)
    expected_date = fields.Datetime(related='order_line_id.date_planned', store=True)
    confirmed_date = fields.Datetime(string="Confirmed date", help="The date the supplier promised.")
    agreed_date = fields.Datetime(compute='_compute_agreed_date', store=True)
    actual_date = fields.Datetime(compute='_compute_receipt_facts', store=True)
    ordered_qty = fields.Float(compute='_compute_quantities', store=True, digits='Product Unit')
    received_qty = fields.Float(compute='_compute_quantities', store=True, digits='Product Unit')
    remaining_qty = fields.Float(compute='_compute_quantities', store=True, digits='Product Unit')
    is_partial = fields.Boolean(string="Partial", compute='_compute_quantities', store=True)
    has_backorder = fields.Boolean(string="Backorder", compute='_compute_receipt_facts', store=True)
    overdue_days = fields.Integer(compute='_compute_overdue_days')
    state = fields.Selection([
        ('open', 'Open'), ('done', 'Received'), ('cancel', 'Cancelled'),
    ], compute='_compute_state', store=True)
    schedule_ids = fields.One2many('procurement.delivery.schedule', 'commitment_id', string="Schedules")

    _order_line_unique = models.Constraint('UNIQUE(order_line_id)', 'A order line has one delivery commitment.')

    def _spm_rounding(self):
        """Return the rounding of the quantities, the product unit precision of the database."""
        return 10 ** -self.env['decimal.precision'].precision_get('Product Unit')

    @api.depends('confirmed_date', 'expected_date')
    def _compute_agreed_date(self):
        """Requirements: BR-SPM-0012"""
        for commitment in self:
            commitment.agreed_date = delivery.agreed(commitment.confirmed_date, commitment.expected_date)

    @api.depends('order_line_id.product_uom_qty', 'order_line_id.qty_received', 'order_line_id.uom_id')
    def _compute_quantities(self):
        """Read the quantities from the order line, which reads Odoo's receipts.

        Requirements: FR-SPM-0055, FR-SPM-0061
        """
        for commitment in self:
            line = commitment.order_line_id
            ordered = line.product_uom_qty
            received = line.qty_received
            commitment.ordered_qty = ordered
            commitment.received_qty = received
            commitment.remaining_qty = max(ordered - received, 0.0)
            commitment.is_partial = received > 0 and commitment.remaining_qty > 0

    @api.depends('order_line_id.move_ids.state', 'order_line_id.move_ids.date',
                 'order_line_id.move_ids.picking_id.backorder_id')
    def _compute_receipt_facts(self):
        """The date of the last done receipt and whether a backorder receipt is open.

        Requirements: FR-SPM-0055, FR-SPM-0059, FR-SPM-0061
        """
        for commitment in self:
            moves = commitment.order_line_id.move_ids.filtered(lambda m: not m._is_purchase_return())
            done = moves.filtered(lambda m: m.state == 'done')
            commitment.actual_date = max(done.mapped('date')) if done else False
            commitment.has_backorder = bool(moves.filtered(
                lambda m: m.state not in ('done', 'cancel') and m.picking_id.backorder_id))

    @api.depends('remaining_qty', 'order_id.state')
    def _compute_state(self):
        for commitment in self:
            if commitment.order_id.state == 'cancel':
                commitment.state = 'cancel'
            elif commitment.remaining_qty <= 0:
                commitment.state = 'done'
            else:
                commitment.state = 'open'

    @api.depends('agreed_date', 'remaining_qty')
    def _compute_overdue_days(self):
        """Days late of a commitment with a remaining quantity whose agreed date is before today.

        Requirements: FR-SPM-0059
        """
        today = fields.Date.context_today(self)
        for commitment in self:
            agreed_day = commitment.agreed_date and fields.Date.context_today(self, commitment.agreed_date)
            commitment.overdue_days = delivery.overdue_days(
                agreed_day, today, commitment.remaining_qty, commitment._spm_rounding())

    def _check_schedule_total(self):
        """Refuse schedules whose quantities do not add up to the ordered quantity.

        Requirements: FR-SPM-0056
        """
        for commitment in self:
            if not commitment.schedule_ids:
                continue
            total = sum(commitment.schedule_ids.mapped('quantity'))
            if not delivery.schedules_match(total, commitment.ordered_qty,
                                            commitment._spm_rounding()):
                raise ValidationError(self.env._(
                    "The schedules total %(total)s of %(ordered)s ordered.",
                    total=f"{total:g}", ordered=f"{commitment.ordered_qty:g}"))

    @api.model_create_multi
    def create(self, vals_list):
        """Check the schedules once the whole commitment is stored.

        Requirements: FR-SPM-0056
        """
        commitments = super(ProcurementDeliveryCommitment, self.with_context(spm_schedule_batch=True)).create(vals_list)
        commitments._check_schedule_total()
        return commitments

    def write(self, vals):
        """Check the schedules once the whole write is done, not after each of them.

        Requirements: FR-SPM-0056
        """
        if 'schedule_ids' not in vals:
            return super().write(vals)
        result = super(ProcurementDeliveryCommitment, self.with_context(spm_schedule_batch=True)).write(vals)
        self._check_schedule_total()
        return result
