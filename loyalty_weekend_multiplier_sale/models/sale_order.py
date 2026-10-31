from odoo import fields, models


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    def action_confirm(self):
        """Compute the points of the order with the day of the confirmation.

        The native confirmation recomputes the points before it stamps the
        confirmation date on the order, so the date is passed in the context.

        Requirements: FR-LAP-009, FR-LAP-014
        """
        return super(SaleOrder, self.with_context(
            lwm_confirmation_date=fields.Datetime.now())).action_confirm()

    def _program_check_compute_points(self, programs):
        """Multiply the points of Loyalty Cards programs on a weekend order.

        The order day is the order date (the confirmation date while
        confirming) in the timezone of the user recording the order; the
        shared core rule decides. Reward costs are never touched.

        Requirements: FR-LAP-007, FR-LAP-012, FR-LAP-014
        """
        result = super()._program_check_compute_points(programs)
        order_date = self.env.context.get('lwm_confirmation_date') or self.date_order
        for program, program_result in result.items():
            if 'points' in program_result:
                program_result['points'] = [
                    program._lwm_weekend_points(points, order_date, self.env.user)
                    for points in program_result['points']
                ]
        return result
