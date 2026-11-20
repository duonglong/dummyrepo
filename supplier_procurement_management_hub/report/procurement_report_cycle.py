from odoo import fields, models
from odoo.tools.sql import SQL


class ProcurementReportSourcing(models.Model):
    """Per sent event: invited and responding suppliers and the time from sending to award.

    Requirements: FR-SPM-0101
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

    @property
    def _table_sql(self) -> SQL:
        """One row per event that was sent; the cycle is empty until the event is awarded.

        Requirements: FR-SPM-0101
        """
        return SQL("""(
            SELECT event.id AS id,
                   event.id AS event_id,
                   event.company_id AS company_id,
                   COALESCE(event.sent_date, MIN(response.create_date)) AS date_sent,
                   event.awarded_date AS date_awarded,
                   COUNT(response.id) AS invited_count,
                   COUNT(response.current_version_id) AS responded_count,
                   EXTRACT(EPOCH FROM event.awarded_date
                           - COALESCE(event.sent_date, MIN(response.create_date))) / 86400.0 AS cycle_days,
                   MIN(response.partner_id) FILTER (
                       WHERE response.state IN ('awarded', 'partly_awarded')) AS awarded_partner_id
              FROM procurement_sourcing_event event
              JOIN procurement_sourcing_response response ON response.event_id = event.id
             WHERE event.state IN ('open', 'awarded')
             GROUP BY event.id)""")


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
    cycle_hours = fields.Float(string="Hours", aggregator='avg', readonly=True)
    state = fields.Selection([
        ('waiting', 'Waiting'), ('open', 'Open'), ('approved', 'Approved'), ('rejected', 'Rejected'),
        ('cancelled', 'Cancelled')], readonly=True)

    @property
    def _table_sql(self) -> SQL:
        """One row per step and approver; the hours run to the final decision, or to now while pending.

        Requirements: FR-SPM-0101
        """
        return SQL("""(
            SELECT ROW_NUMBER() OVER (ORDER BY step.id, rel.user_id) AS id,
                   step.id AS step_id,
                   request.company_id AS company_id,
                   rel.user_id AS approver_id,
                   request.submitted_date AS submitted_date,
                   request.decided_date AS decided_date,
                   EXTRACT(EPOCH FROM COALESCE(request.decided_date, NOW() AT TIME ZONE 'UTC')
                           - request.submitted_date) / 3600.0 AS cycle_hours,
                   step.state AS state
              FROM procurement_approval_step step
              JOIN procurement_approval_request request ON request.id = step.request_id
              JOIN procurement_approval_step_user_rel rel ON rel.step_id = step.id)""")
