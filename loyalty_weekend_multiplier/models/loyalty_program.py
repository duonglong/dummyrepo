import pytz

from odoo import api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools import float_round

WEEKEND_WEEKDAYS = (5, 6)  # Saturday and Sunday, not configurable (BR-LAP-001)


class LoyaltyProgram(models.Model):
    _inherit = 'loyalty.program'

    weekend_multiplier = fields.Float(
        string='Weekend Multiplier',
        digits=(16, 2),
        default=1.0,
        required=True,
        help="Factor applied to the points a Loyalty Cards program earns on "
             "Saturday and Sunday orders. 1.00 earns the same points as a weekday.",
    )

    @api.constrains('weekend_multiplier')
    def _check_weekend_multiplier(self):
        """Refuse a Weekend Multiplier lower than or equal to 0.

        Requirements: FR-LAP-001, FR-LAP-002, BR-LAP-002
        """
        if any(program.weekend_multiplier <= 0 for program in self):
            raise ValidationError(self.env._(
                "The Weekend Multiplier must be greater than 0."
            ))

    @api.model
    def _lwm_is_weekend(self, order_datetime, user):
        """Tell whether an order date falls on Saturday or Sunday.

        ``order_datetime`` is a naive UTC datetime; the day is read in the
        timezone of ``user``, UTC when that user has no timezone. Shared by
        Sales and Point of Sale.

        Requirements: BR-LAP-001, BR-LAP-004, FR-LAP-005
        """
        local_datetime = pytz.utc.localize(order_datetime).astimezone(
            pytz.timezone(user.tz or 'UTC'))
        return local_datetime.weekday() in WEEKEND_WEEKDAYS

    def _lwm_apply_multiplier(self, points):
        """Multiply points earned by this program, rounded down to 2 decimals.

        Requirements: FR-LAP-003, FR-LAP-006, BR-LAP-003
        """
        self.ensure_one()
        return float_round(
            points * self.weekend_multiplier,
            precision_digits=2,
            rounding_method='DOWN',
        )
