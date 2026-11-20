from odoo import fields, models
from odoo.tools.sql import SQL
from odoo.tools.translate import LazyTranslate

_lt = LazyTranslate(__name__)


class ProcurementReportDelivery(models.Model):
    """Delivery commitments that are due, with their on-time and received quantities.

    Requirements: FR-SPM-0102
    A commitment is due once its agreed date has passed or it is received. It is on time when its
    last receipt came on or before the agreed day plus the company's tolerance (UTC days). The
    on-time rate is on-time lines over due lines and the fulfillment rate is received over
    ordered quantity, so both stay exact under any grouping.
    """
    _name = 'procurement.report.delivery'
    _description = 'Delivery Performance Report'
    _auto = False
    _order = 'date desc, id desc'
    # (label, numerator, denominator, factor): rates the printed report adds to its figures
    _spm_rates = (
        (_lt('On time (%)'), 'on_time_count', 'line_count', 100.0),
        (_lt('Fulfillment (%)'), 'received_qty', 'ordered_qty', 100.0))

    commitment_id = fields.Many2one('procurement.delivery.commitment', readonly=True)
    date = fields.Date(string="Agreed date", readonly=True)
    company_id = fields.Many2one('res.company', readonly=True)
    partner_id = fields.Many2one('res.partner', string="Supplier", readonly=True)
    product_id = fields.Many2one('product.product', readonly=True)
    order_id = fields.Many2one('purchase.order', readonly=True)
    line_count = fields.Integer(string="Due lines", readonly=True)
    on_time_count = fields.Integer(string="On time", readonly=True)
    ordered_qty = fields.Float(string="Ordered", digits='Product Unit', readonly=True)
    received_qty = fields.Float(string="Received", digits='Product Unit', readonly=True)

    @property
    def _table_sql(self) -> SQL:
        """One row per due, non-cancelled commitment.

        Requirements: FR-SPM-0102
        """
        return SQL("""(
            SELECT commitment.id AS id,
                   commitment.id AS commitment_id,
                   commitment.agreed_date::date AS date,
                   commitment.company_id AS company_id,
                   commitment.partner_id AS partner_id,
                   commitment.product_id AS product_id,
                   commitment.order_id AS order_id,
                   1 AS line_count,
                   CASE WHEN commitment.state = 'done'
                         AND commitment.actual_date::date
                             <= commitment.agreed_date::date + COALESCE(company.spm_on_time_tolerance_days, 0)
                        THEN 1 ELSE 0 END AS on_time_count,
                   commitment.ordered_qty AS ordered_qty,
                   commitment.received_qty AS received_qty
              FROM procurement_delivery_commitment commitment
              JOIN res_company company ON company.id = commitment.company_id
             WHERE commitment.state != 'cancel'
               AND commitment.agreed_date IS NOT NULL
               AND (commitment.state = 'done' OR commitment.agreed_date < NOW() AT TIME ZONE 'UTC'))""")
