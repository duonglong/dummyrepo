from odoo import fields, models


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    def action_confirm(self):
        """Compute the points of the order with the day of the confirmation.

        The native confirmation recomputes the points before it stamps the
        confirmation date on the order, so the date is passed in the context.

        Requirements: FR-LAP-009, BR-LAP-004
        """
        return super(SaleOrder, self.with_context(
            lwm_confirmation_date=fields.Datetime.now())).action_confirm()

    def _program_check_compute_points(self, programs):
        """Multiply the points of Loyalty Cards programs on a weekend order.

        The order day is the order date in the timezone of the user recording
        the order; weekday orders keep the native points untouched.

        Requirements: FR-LAP-003, FR-LAP-004, FR-LAP-007, FR-LAP-010, FR-LAP-012, BR-LAP-003, BR-LAP-005
        """
        result = super()._program_check_compute_points(programs)
        order_date = self.env.context.get('lwm_confirmation_date') or self.date_order
        if not self.env['loyalty.program']._lwm_is_weekend(order_date, self.env.user):
            return result
        for program, program_result in result.items():
            if program.program_type == 'loyalty' and 'points' in program_result:
                program_result['points'] = [
                    program._lwm_apply_multiplier(points)
                    for points in program_result['points']
                ]
        return result
