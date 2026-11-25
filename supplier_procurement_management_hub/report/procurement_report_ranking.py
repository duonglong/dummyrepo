from odoo import fields, models
from odoo.tools.sql import SQL


class ProcurementReportRanking(models.Model):
    """Supplier rankings by period, read from the scorecard snapshots.

    Requirements: FR-SPM-0102, NFR-SPM-0006
    Suppliers are ranked by score within one company, template and period, so the ranking agrees
    with the scorecards told to suppliers in reviews. Equal scores share a rank. Snapshots without
    a score are left out.
    """
    _name = 'procurement.report.ranking'
    _description = 'Supplier Ranking Report'
    _auto = False
    _order = 'period_start desc, template_id, rank, id'

    scorecard_id = fields.Many2one('procurement.supplier.scorecard', readonly=True)
    partner_id = fields.Many2one('res.partner', string="Supplier", readonly=True)
    template_id = fields.Many2one('procurement.scorecard.template', string="Template", readonly=True)
    company_id = fields.Many2one('res.company', readonly=True)
    period_start = fields.Date(string="Period start", readonly=True)
    period_end = fields.Date(string="Period end", readonly=True)
    score = fields.Float(readonly=True, aggregator='avg')
    range = fields.Selection([('good', 'Good'), ('acceptable', 'Acceptable'), ('poor', 'Poor')], readonly=True)
    rank = fields.Integer(readonly=True, aggregator=False,
                          help="Position by score within the template and period; equal scores share a rank.")

    @property
    def _table_sql(self) -> SQL:
        """One row per scored scorecard, ranked within its company, template and period.

        Requirements: FR-SPM-0102
        """
        return SQL("""(
            SELECT card.id AS id,
                   card.id AS scorecard_id,
                   card.partner_id AS partner_id,
                   card.template_id AS template_id,
                   card.company_id AS company_id,
                   card.period_start AS period_start,
                   card.period_end AS period_end,
                   card.score AS score,
                   card.range AS range,
                   RANK() OVER (PARTITION BY card.company_id, card.template_id, card.period_start
                                ORDER BY card.score DESC) AS rank
              FROM procurement_supplier_scorecard card
             WHERE card.score IS NOT NULL)""")
