from odoo import api, fields, models
from odoo.tools.sql import SQL

from ..services import report
from .procurement_report_spend import CONFIRMED_STATES


class ProcurementReportPrice(models.Model):
    """Unit price per product, supplier and period in the company currency and the product unit.

    Requirements: FR-SPM-0100
    """
    _name = 'procurement.report.price'
    _description = 'Price Report'
    _auto = False
    _order = 'date desc, id desc'

    date = fields.Date(string="Quarter", readonly=True, help="First day of the quarter.")
    company_id = fields.Many2one('res.company', readonly=True)
    partner_id = fields.Many2one('res.partner', string="Supplier", readonly=True)
    product_id = fields.Many2one('product.product', readonly=True)
    currency_id = fields.Many2one('res.currency', related='company_id.currency_id', readonly=True)
    price_unit = fields.Monetary(
        string="Unit price", currency_field='currency_id', aggregator=None, readonly=True,
        help="Quantity-weighted price of the supplier for one product in the quarter. Not summed or averaged "
             "above that row; amount over quantity gives the weighted price of any grouping.")
    quantity = fields.Float(string="Quantity (product unit)", readonly=True)
    amount = fields.Monetary(
        string="Amount", currency_field='currency_id', readonly=True,
        help="Unit price times quantity; amount over quantity is the quantity-weighted price of any grouping.")
    price_variance = fields.Monetary(
        string="Variance", currency_field='currency_id', aggregator=None, readonly=True,
        help="Supplier price minus the quantity-weighted average of the product in the quarter. "
             "Shown per row only: grouped lines do not combine variances.")

    @property
    def _table_sql(self) -> SQL:
        """One row per company, product, supplier and quarter: weighted price per product unit, company currency.

        Requirements: FR-SPM-0100
        """
        return SQL("""(
            SELECT ROW_NUMBER() OVER (ORDER BY supplier_quarter.company_id, supplier_quarter.product_id,
                                               supplier_quarter.partner_id, supplier_quarter.date) AS id,
                   supplier_quarter.date, supplier_quarter.company_id, supplier_quarter.partner_id,
                   supplier_quarter.product_id, supplier_quarter.quantity, supplier_quarter.amount,
                   supplier_quarter.amount / supplier_quarter.quantity AS price_unit,
                   supplier_quarter.amount / supplier_quarter.quantity
                       - SUM(supplier_quarter.amount) OVER quarter
                         / SUM(supplier_quarter.quantity) OVER quarter AS price_variance
              FROM (
                SELECT DATE_TRUNC('quarter', order_.date_order)::date AS date,
                       order_.company_id AS company_id,
                       order_.partner_id AS partner_id,
                       line.product_id AS product_id,
                       SUM(line.product_uom_qty) AS quantity,
                       SUM(line.price_unit * (1 - COALESCE(line.discount, 0) / 100.0) * line.product_qty
                           / NULLIF(line.product_uom_qty, 0)
                           / COALESCE(NULLIF(order_.currency_rate, 0), 1.0) * line.product_uom_qty) AS amount
                  FROM purchase_order_line line
                  JOIN purchase_order order_ ON order_.id = line.order_id
                 WHERE line.display_type IS NULL
                   AND line.product_id IS NOT NULL
                   AND line.product_uom_qty > 0
                   AND order_.state IN %s
                 GROUP BY DATE_TRUNC('quarter', order_.date_order), order_.company_id, order_.partner_id,
                          line.product_id) AS supplier_quarter
            WINDOW quarter AS (PARTITION BY supplier_quarter.company_id, supplier_quarter.product_id,
                                            supplier_quarter.date))""", CONFIRMED_STATES)

    @api.model
    def spm_price_trend(self, product, date_from, date_to):
        """Return per supplier the price, the period average and the variance of a product.

        Requirements: FR-SPM-0100
        """
        return report.price_trend(self.env, product, date_from, date_to)
