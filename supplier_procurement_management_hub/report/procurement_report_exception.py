from odoo import api, fields, models
from odoo.tools.sql import SQL

from ..models.procurement_exception_route import EXCEPTION_KINDS

STAGES = [('order_to_receipt', 'Order to receipt'), ('receipt_to_bill', 'Receipt to bill')]


class ProcurementReportException(models.Model):
    """Matched lines and exceptions per period, stage, kind and supplier.

    Requirements: FR-SPM-0102
    Order to receipt: delivery commitments are the matched lines and delivery discrepancies the
    exceptions. Receipt to bill: matching lines of vendor bills and their matching exceptions. A
    row is either a matched line (``line_count`` 1) or an exception (``exception_count`` 1), so
    both sum exactly under any grouping; the kind is known on exception rows only.
    """
    _name = 'procurement.report.exception'
    _description = 'Exception Rate Report'
    _auto = False
    _order = 'date desc, id desc'

    date = fields.Date(readonly=True)
    company_id = fields.Many2one('res.company', readonly=True)
    partner_id = fields.Many2one('res.partner', string="Supplier", readonly=True)
    kind = fields.Selection(EXCEPTION_KINDS, readonly=True)
    stage = fields.Selection(STAGES, readonly=True)
    line_count = fields.Integer(string="Matched lines", readonly=True)
    exception_count = fields.Integer(string="Exceptions", readonly=True)
    exception_rate = fields.Float(
        string="Per hundred lines", digits=(16, 2), readonly=True,
        help="Exceptions per hundred matched lines; under any grouping it is the sum of the exceptions "
             "over the sum of the matched lines, never an average of rates.")

    @property
    def _table_sql(self) -> SQL:
        """One row per matched line and per exception of both matching stages.

        Requirements: FR-SPM-0102
        """
        return SQL("""(
            SELECT ROW_NUMBER() OVER (ORDER BY rows.stage, rows.date, rows.source_id, rows.line_count) AS id,
                   rows.date, rows.company_id, rows.partner_id, rows.kind, rows.stage,
                   rows.line_count, rows.exception_count,
                   100.0 * rows.exception_count / NULLIF(rows.line_count, 0) AS exception_rate
              FROM (
                SELECT COALESCE(move.invoice_date, move.date) AS date, recon.company_id, recon.partner_id,
                       NULL::varchar AS kind, 'receipt_to_bill' AS stage, 1 AS line_count,
                       0 AS exception_count, line.id AS source_id
                  FROM procurement_reconciliation_line line
                  JOIN procurement_reconciliation recon ON recon.id = line.reconciliation_id
                  JOIN account_move move ON move.id = recon.move_id
                UNION ALL
                SELECT COALESCE(move.invoice_date, move.date), recon.company_id, recon.partner_id,
                       exception.kind, 'receipt_to_bill', 0, 1, exception.id
                  FROM procurement_reconciliation_exception exception
                  JOIN procurement_reconciliation recon ON recon.id = exception.reconciliation_id
                  JOIN account_move move ON move.id = recon.move_id
                UNION ALL
                SELECT commitment.agreed_date::date, commitment.company_id, commitment.partner_id,
                       NULL, 'order_to_receipt', 1, 0, commitment.id
                  FROM procurement_delivery_commitment commitment
                 WHERE commitment.state != 'cancel' AND commitment.agreed_date IS NOT NULL
                   AND (commitment.state = 'done' OR commitment.agreed_date < NOW() AT TIME ZONE 'UTC')
                UNION ALL
                SELECT discrepancy.create_date::date, discrepancy.company_id, discrepancy.partner_id,
                       NULL, 'order_to_receipt', 0, 1, discrepancy.id
                  FROM procurement_delivery_discrepancy discrepancy
              ) AS rows)""")

    def _read_group_select(self, table, aggregate_spec):
        """Aggregate the rate as total exceptions over total lines, not as a sum of row rates.

        Requirements: FR-SPM-0102
        """
        if aggregate_spec == 'exception_rate:sum':
            return SQL(
                "100.0 * SUM(%s) / NULLIF(SUM(%s), 0)", table['exception_count'], table['line_count'])
        return super()._read_group_select(table, aggregate_spec)

    @api.model
    def spm_exception_rate(self, date_from, date_to, stage='receipt_to_bill'):
        """Return the exceptions per hundred matched lines of a stage in a period, as the caller sees them.

        Requirements: FR-SPM-0102
        """
        domain = [('stage', '=', stage), ('date', '>=', date_from), ('date', '<=', date_to)]
        ((lines, exceptions),) = self._read_group(domain, [], ['line_count:sum', 'exception_count:sum'])
        return 100.0 * exceptions / lines if lines else 0.0
