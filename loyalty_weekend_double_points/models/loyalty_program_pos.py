from odoo import models


class LoyaltyProgram(models.Model):
    _inherit = 'loyalty.program'

    def _get_pos_order_points(self, order, lines=None):
        """Requirements: FR-DLP-003, FR-DLP-006, BR-DLP-001, NFR-REL-001.

        Double the points earned by a point of sale order placed on a weekend. A refund
        reverses the points recorded in the history, so only earning is doubled here.
        """
        points = super()._get_pos_order_points(order, lines)
        return points * self._get_weekend_factor(order.date_order, order.company_id)
