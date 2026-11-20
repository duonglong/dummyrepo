from odoo import fields, models
from odoo.tools.sql import SQL

from ..models.procurement_sourcing_event import SPM_PRICE_GROUPS
from ..services.report import BASELINE_KINDS


class ProcurementReportSourcing(models.Model):
    """Per sent event: invited and responding suppliers and the time from sending to award.

    Requirements: FR-SPM-0101, FR-SPM-0103
    """
    _name = 'procurement.report.sourcing'
    _description = 'Sourcing Cycle Report'
    _auto = False
    _order = 'date_sent desc, id desc'

    event_id = fields.Many2one('procurement.sourcing.event', readonly=True)
    company_id = fields.Many2one('res.company', readonly=True)
    awarded_partner_id = fields.Many2one('res.partner', string="Awarded supplier", readonly=True)
    date_sent = fields.Datetime(readonly=True)
    date_awarded = fields.Datetime(readonly=True)
    invited_count = fields.Integer(string="Invited", readonly=True)
    responded_count = fields.Integer(string="Answered", readonly=True)
    cycle_days = fields.Float(string="Days to award", aggregator='avg', readonly=True)
    currency_id = fields.Many2one('res.currency', related='company_id.currency_id', readonly=True)
    baseline_kind = fields.Selection(BASELINE_KINDS, readonly=True)
    baseline_amount = fields.Monetary(
        currency_field='currency_id', readonly=True, groups=SPM_PRICE_GROUPS,
        help="What the awarded total is compared with, in the company currency.")
    awarded_amount = fields.Monetary(currency_field='currency_id', readonly=True, groups=SPM_PRICE_GROUPS)
    savings_amount = fields.Monetary(
        currency_field='currency_id', readonly=True, groups=SPM_PRICE_GROUPS,
        help="Baseline minus awarded total; the baseline kind is shown beside it. Counted once per event.")

    @property
    def _table_sql(self) -> SQL:
        """One row per sent event and awarded supplier (one row when nothing is awarded yet).

        Requirements: FR-SPM-0101, FR-SPM-0103
        The counts and the cycle sit on the first row of an event only, so a split award neither
        repeats the invitations nor weights the average cycle by the number of winners.
        """
        return SQL("""(
            WITH base AS (
                SELECT event.id AS event_id,
                       event.company_id AS company_id,
                       COALESCE(event.sent_date, MIN(response.create_date)) AS date_sent,
                       event.awarded_date AS date_awarded,
                       event.baseline_kind AS baseline_kind,
                       event.baseline_amount AS baseline_amount,
                       event.awarded_amount AS awarded_amount,
                       event.savings_amount AS savings_amount,
                       COUNT(response.id) AS invited_count,
                       COUNT(response.current_version_id) AS responded_count
                  FROM procurement_sourcing_event event
                  JOIN procurement_sourcing_response response ON response.event_id = event.id
                 WHERE event.state IN ('open', 'awarded')
                 GROUP BY event.id),
            winner AS (
                SELECT response.event_id, response.partner_id
                  FROM procurement_sourcing_response response
                 WHERE response.state IN ('awarded', 'partly_awarded')
                 GROUP BY response.event_id, response.partner_id),
            joined AS (
                SELECT base.*, winner.partner_id,
                       ROW_NUMBER() OVER (PARTITION BY base.event_id ORDER BY winner.partner_id) = 1 AS first_row
                  FROM base
                  LEFT JOIN winner ON winner.event_id = base.event_id)
            SELECT ROW_NUMBER() OVER (ORDER BY joined.event_id, joined.partner_id) AS id,
                   joined.event_id AS event_id,
                   joined.company_id AS company_id,
                   joined.partner_id AS awarded_partner_id,
                   joined.date_sent AS date_sent,
                   joined.date_awarded AS date_awarded,
                   CASE WHEN joined.first_row THEN joined.invited_count ELSE 0 END AS invited_count,
                   CASE WHEN joined.first_row THEN joined.responded_count ELSE 0 END AS responded_count,
                   CASE WHEN joined.first_row
                        THEN EXTRACT(EPOCH FROM joined.date_awarded - joined.date_sent) / 86400.0
                   END AS cycle_days,
                   joined.baseline_kind AS baseline_kind,
                   CASE WHEN joined.first_row THEN joined.baseline_amount END AS baseline_amount,
                   CASE WHEN joined.first_row THEN joined.awarded_amount END AS awarded_amount,
                   CASE WHEN joined.first_row THEN joined.savings_amount END AS savings_amount
              FROM joined)""")


class ProcurementReportApproval(models.Model):
    """Per approval step and approver: submission, decision and waiting age.

    Requirements: FR-SPM-0101
    """
    _name = 'procurement.report.approval'
    _description = 'Approval Cycle Report'
    _auto = False
    _order = 'submitted_date desc, id desc'

    step_id = fields.Many2one('procurement.approval.step', readonly=True)
    company_id = fields.Many2one('res.company', readonly=True)
    approver_id = fields.Many2one('res.users', readonly=True)
    submitted_date = fields.Datetime(readonly=True)
    decided_date = fields.Datetime(readonly=True)
    cycle_hours = fields.Float(
        string="Cycle hours", aggregator='avg', readonly=True,
        help="Submission to final decision of a decided request; counted once per request.")
    waiting_hours = fields.Float(
        string="Waiting hours", aggregator='avg', readonly=True,
        help="Time an open step has waited since it opened, for an approver who has not approved yet.")
    state = fields.Selection([
        ('waiting', 'Waiting'), ('open', 'Open'), ('approved', 'Approved'), ('rejected', 'Rejected'),
        ('cancelled', 'Cancelled')], readonly=True)

    @property
    def _table_sql(self) -> SQL:
        """One row per step and approver; cycle hours count once per decided request, waiting hours run from the step opening.

        Requirements: FR-SPM-0101
        """
        return SQL("""(
            SELECT ROW_NUMBER() OVER (ORDER BY step.id, rel.user_id) AS id,
                   step.id AS step_id,
                   request.company_id AS company_id,
                   rel.user_id AS approver_id,
                   request.submitted_date AS submitted_date,
                   request.decided_date AS decided_date,
                   CASE WHEN request.state IN ('approved', 'rejected')
                         AND ROW_NUMBER() OVER (PARTITION BY request.id ORDER BY step.id, rel.user_id) = 1
                        THEN EXTRACT(EPOCH FROM request.decided_date - request.submitted_date) / 3600.0
                   END AS cycle_hours,
                   CASE WHEN step.state = 'open' AND step.opened_date IS NOT NULL
                         AND NOT EXISTS (SELECT 1 FROM procurement_approval_decision decided
                                          WHERE decided.step_id = step.id AND decided.decision = 'approved'
                                            AND COALESCE(decided.on_behalf_of_id, decided.approver_id) = rel.user_id)
                        THEN EXTRACT(EPOCH FROM NOW() AT TIME ZONE 'UTC' - step.opened_date) / 3600.0
                   END AS waiting_hours,
                   CASE WHEN step.state = 'open' AND EXISTS (
                            SELECT 1 FROM procurement_approval_decision decided
                             WHERE decided.step_id = step.id AND decided.decision = 'approved'
                               AND COALESCE(decided.on_behalf_of_id, decided.approver_id) = rel.user_id)
                        THEN 'approved' ELSE step.state END AS state
              FROM procurement_approval_step step
              JOIN procurement_approval_request request ON request.id = step.request_id
              JOIN procurement_approval_step_user_rel rel ON rel.step_id = step.id)""")
