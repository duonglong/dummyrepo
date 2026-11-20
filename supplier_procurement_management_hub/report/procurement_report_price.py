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

    date = fields.Date(readonly=True)
    company_id = fields.Many2one('res.company', readonly=True)
    partner_id = fields.Many2one('res.partner', string="Supplier", readonly=True)
    product_id = fields.Many2one('product.product', readonly=True)
    currency_id = fields.Many2one('res.currency', related='company_id.currency_id', readonly=True)
    price_unit = fields.Monetary(string="Unit price", currency_field='currency_id', aggregator='avg', readonly=True)
    quantity = fields.Float(string="Quantity (product unit)", readonly=True)

    @property
    def _table_sql(self) -> SQL:
        """One row per priced line of a confirmed order: discounted price per product unit, company currency.

        Requirements: FR-SPM-0100
        """
        return SQL("""(
            SELECT line.id AS id,
                   order_.date_order::date AS date,
                   order_.company_id AS company_id,
                   order_.partner_id AS partner_id,
                   line.product_id AS product_id,
                   line.price_unit * (1 - COALESCE(line.discount, 0) / 100.0) * line.product_qty
                       / NULLIF(line.product_uom_qty, 0)
                       / COALESCE(NULLIF(order_.currency_rate, 0), 1.0) AS price_unit,
                   line.product_uom_qty AS quantity
              FROM purchase_order_line line
              JOIN purchase_order order_ ON order_.id = line.order_id
             WHERE line.display_type IS NULL
               AND line.product_id IS NOT NULL
               AND line.product_uom_qty > 0
               AND order_.state IN %s)""", CONFIRMED_STATES)

    @api.model
    def spm_price_trend(self, product, date_from, date_to):
        """Return per supplier the price, the period average and the variance of a product.

        Requirements: FR-SPM-0100
        """
        return report.price_trend(self.env, product, date_from, date_to)
