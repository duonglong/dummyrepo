from odoo import fields, models
from odoo.tools.sql import SQL

CONFIRMED_STATES = ('purchase', 'done')


class ProcurementReportSpend(models.Model):
    """Spend of confirmed purchase order lines in the company currency.

    Requirements: FR-SPM-0099
    """
    _name = 'procurement.report.spend'
    _description = 'Spend Report'
    _auto = False
    _order = 'date desc, id desc'

    date = fields.Date(readonly=True)
    company_id = fields.Many2one('res.company', readonly=True)
    currency_id = fields.Many2one('res.currency', related='company_id.currency_id', readonly=True)
    partner_id = fields.Many2one('res.partner', string="Supplier", readonly=True)
    product_id = fields.Many2one('product.product', readonly=True)
    categ_id = fields.Many2one('product.category', string="Product category", readonly=True)
    department_id = fields.Many2one('hr.department', readonly=True)
    order_id = fields.Many2one('purchase.order', readonly=True)
    amount_untaxed = fields.Monetary(string="Untaxed amount", currency_field='currency_id', readonly=True)

    @property
    def _table_sql(self) -> SQL:
        """One row per non-cancelled line of a confirmed order, the amount converted to the company currency.

        Requirements: FR-SPM-0099
        The department is that of the order's single purchase request, else of its requester's employee.
        """
        return SQL("""(
            SELECT line.id AS id,
                   order_.date_order::date AS date,
                   order_.company_id AS company_id,
                   order_.partner_id AS partner_id,
                   line.product_id AS product_id,
                   template.categ_id AS categ_id,
                   COALESCE(
                       (SELECT MIN(request.department_id)
                          FROM procurement_purchase_order_request_rel rel
                          JOIN procurement_request request ON request.id = rel.request_id
                         WHERE rel.order_id = order_.id
                        HAVING COUNT(DISTINCT request.department_id) = 1),
                       (SELECT version.department_id
                          FROM hr_employee employee
                          JOIN hr_version version ON version.id = employee.current_version_id
                         WHERE employee.user_id = COALESCE(order_.spm_requester_id, order_.user_id)
                           AND employee.company_id = order_.company_id
                         ORDER BY employee.id LIMIT 1)) AS department_id,
                   order_.id AS order_id,
                   line.price_subtotal / COALESCE(NULLIF(order_.currency_rate, 0), 1.0) AS amount_untaxed
              FROM purchase_order_line line
              JOIN purchase_order order_ ON order_.id = line.order_id
              LEFT JOIN product_product product ON product.id = line.product_id
              LEFT JOIN product_template template ON template.id = product.product_tmpl_id
             WHERE line.display_type IS NULL
               AND order_.state IN %s)""", CONFIRMED_STATES)
