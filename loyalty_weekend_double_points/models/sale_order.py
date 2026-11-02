from odoo import fields, models


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    def _program_check_compute_points(self, programs):
        """Requirements: FR-DLP-002, FR-DLP-005, NFR-REL-001.

        Double the points earned by weekend programs, after the native computation.
        Reward costs are never touched: only the earned points are multiplied.
        """
        result = super()._program_check_compute_points(programs)
        self.ensure_one()
        order_date = self.date_order if self.state == 'sale' else fields.Datetime.now()
        for program, status in result.items():
            if 'points' not in status:
                continue
            factor = program._get_weekend_factor(order_date, self.company_id)
            if factor != 1:
                status['points'] = [points * factor for points in status['points']]
        return result
