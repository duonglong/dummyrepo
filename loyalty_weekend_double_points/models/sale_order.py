from odoo import fields, models


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    def _get_weekend_reference_datetime(self):
        """Requirements: FR-DLP-002, NFR-REL-001.

        The purchase moment: the earliest done/authorized payment, else the order
        date once confirmed, else now (same filter as native confirmed tx date).
        """
        self.ensure_one()
        tx_dates = self.sudo().transaction_ids.filtered(
            lambda tx: tx.state in ('done', 'authorized')
        ).mapped('create_date')
        if tx_dates:
            return min(tx_dates)
        return self.date_order if self.state == 'sale' else fields.Datetime.now()

    def _program_check_compute_points(self, programs):
        """Requirements: FR-DLP-002, FR-DLP-005, NFR-REL-001.

        Double the points earned by weekend programs, after the native computation.
        Reward costs are never touched: only the earned points are multiplied.
        """
        result = super()._program_check_compute_points(programs)
        self.ensure_one()
        order_date = self._get_weekend_reference_datetime()
        for program, status in result.items():
            if 'points' not in status:
                continue
            factor = program._get_weekend_factor(order_date, self.company_id)
            if factor != 1:
                status['points'] = [points * factor for points in status['points']]
        return result
