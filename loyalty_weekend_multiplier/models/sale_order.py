import pytz

from odoo import fields, models

WEEKEND_WEEKDAYS = (5, 6)  # Saturday and Sunday (BR-LAP-001)


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    def _is_weekend_order(self):
        """Tell whether the order day is Saturday or Sunday.

        The day is the order date in the timezone of the user recording the
        order, UTC when that user has no timezone.

        Requirements: BR-LAP-001, BR-LAP-004, FR-LAP-005
        """
        self.ensure_one()
        tz = pytz.timezone(self.env.user.tz or 'UTC')
        date_order = fields.Datetime.to_datetime(self.date_order) or fields.Datetime.now()
        local_date = pytz.utc.localize(date_order).astimezone(tz)
        return local_date.weekday() in WEEKEND_WEEKDAYS

    def _program_check_compute_points(self, programs):
        """Multiply the points of Loyalty Cards programs on a weekend order.

        Requirements: FR-LAP-003, FR-LAP-004, FR-LAP-007, BR-LAP-003
        """
        result = super()._program_check_compute_points(programs)
        if self._is_weekend_order():
            for program, program_result in result.items():
                if program.program_type == 'loyalty' and 'points' in program_result:
                    program_result['points'] = [
                        program._apply_weekend_multiplier(points)
                        for points in program_result['points']
                    ]
        return result
