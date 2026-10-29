from odoo import models


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    def _program_check_compute_points(self, programs):
        """Multiply the points of Loyalty Cards programs on a weekend order.

        The order day is the order date in the timezone of the user recording
        the order; weekday orders keep the native points untouched.

        Requirements: FR-LAP-003, FR-LAP-004, FR-LAP-007, BR-LAP-003
        """
        result = super()._program_check_compute_points(programs)
        if not self.env['loyalty.program']._lwm_is_weekend(self.date_order, self.env.user):
            return result
        for program, program_result in result.items():
            if program.program_type == 'loyalty' and 'points' in program_result:
                program_result['points'] = [
                    program._lwm_apply_multiplier(points)
                    for points in program_result['points']
                ]
        return result
