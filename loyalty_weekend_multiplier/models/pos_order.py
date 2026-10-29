from odoo import api, models


class LoyaltyProgramPos(models.Model):
    _inherit = 'loyalty.program'

    @api.model
    def _load_pos_data_fields(self, config):
        """Send the Weekend Multiplier to the point of sale.

        Requirements: FR-LAP-008
        """
        return super()._load_pos_data_fields(config) + ['weekend_multiplier']

    def _get_pos_order_points(self, order, lines=None):
        """Multiply the points a Point of Sale order earns on a weekend.

        The day is the day of the order that earned the points: a refund
        reads the points given by the original order from its history, so
        the refund day never changes them.

        Requirements: FR-LAP-008, FR-LAP-011, BR-LAP-003, BR-LAP-004
        """
        points = super()._get_pos_order_points(order, lines)
        if (self.program_type == 'loyalty' and points
                and self._lwm_is_weekend(order.date_order, self.env.user)):
            return self._lwm_apply_multiplier(points)
        return points
