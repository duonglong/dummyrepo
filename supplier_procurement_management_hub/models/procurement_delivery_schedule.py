from odoo import api, fields, models

from ..services import delivery


class ProcurementDeliverySchedule(models.Model):
    """A part delivery of a commitment with its own date and quantity.

    Requirements: FR-SPM-0056, FR-SPM-0059
    The quantity received by the commitment is spread over its schedules in date order.
    """
    _name = 'procurement.delivery.schedule'
    _description = 'Delivery Schedule'
    _order = 'date, id'

    commitment_id = fields.Many2one('procurement.delivery.commitment', string="Commitment", required=True,
                                    ondelete='cascade', index=True)
    company_id = fields.Many2one(related='commitment_id.company_id', store=True, index=True)
    date = fields.Datetime(required=True)
    quantity = fields.Float(digits='Product Unit')
    received_qty = fields.Float(compute='_compute_received', digits='Product Unit')
    remaining_qty = fields.Float(compute='_compute_received', digits='Product Unit')
    overdue_days = fields.Integer(compute='_compute_received')

    _quantity_positive = models.Constraint('CHECK(quantity > 0)', 'A schedule quantity must be positive.')

    @api.depends('quantity', 'date', 'commitment_id.received_qty', 'commitment_id.schedule_ids.quantity',
                 'commitment_id.schedule_ids.date')
    def _compute_received(self):
        """Received, remaining and late days of each schedule.

        Requirements: FR-SPM-0056, FR-SPM-0059
        """
        today = fields.Date.context_today(self)
        for commitment in self.commitment_id:
            schedules = commitment.schedule_ids.sorted(lambda s: (s.date or fields.Datetime.now(), s.id))
            shares = delivery.allocate(commitment.received_qty, schedules.mapped('quantity'))
            rounding = commitment._spm_rounding()
            for schedule, share in zip(schedules, shares):
                remaining = schedule.quantity - share
                schedule.received_qty = share
                schedule.remaining_qty = remaining
                day = schedule.date and fields.Date.context_today(self, schedule.date)
                schedule.overdue_days = delivery.overdue_days(day, today, remaining, rounding)
        for schedule in self - self.commitment_id.schedule_ids:
            schedule.received_qty = schedule.remaining_qty = schedule.overdue_days = 0

    @api.model_create_multi
    def create(self, vals_list):
        """Requirements: FR-SPM-0056"""
        schedules = super().create(vals_list)
        schedules.commitment_id._spm_check_outside_batch()
        return schedules

    def write(self, vals):
        """Requirements: FR-SPM-0056"""
        commitments = self.commitment_id
        result = super().write(vals)
        if 'quantity' in vals or 'commitment_id' in vals:
            (self.commitment_id | commitments)._spm_check_outside_batch()
        return result

    def unlink(self):
        """Requirements: FR-SPM-0056"""
        commitments = self.commitment_id
        result = super().unlink()
        commitments._spm_check_outside_batch()
        return result
