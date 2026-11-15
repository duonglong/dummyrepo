from datetime import timedelta

from odoo import fields, models

from ..services import report


class DataQualityReportWizard(models.TransientModel):
    """The choices of the quality report: companies, dates, trend period and file format.

    Requirements: FR-DGQ-0078
    """
    _name = 'data.quality.report.wizard'
    _description = 'Quality Report Dialog'

    company_ids = fields.Many2many(
        'res.company', string='Companies', default=lambda self: self.env.companies,
        help="The report covers these companies, among the ones you work with.")
    date_from = fields.Date(
        string='From', required=True, default=lambda self: fields.Date.today() - timedelta(days=90),
        help="First day of the trend and of the resolution time.")
    date_to = fields.Date(
        string='To', required=True, default=fields.Date.today,
        help="Scores and issues are those of the end of this day.")
    period = fields.Selection(
        [('week', 'Week'), ('month', 'Month')], string='Trend by', required=True, default='month')
    file_format = fields.Selection(
        [('xlsx', 'XLSX'), ('pdf', 'PDF')], string='Format', required=True, default='pdf')

    def _report_data(self):
        """The figures of the report for the chosen companies and dates, within the user's access.

        Requirements: FR-DGQ-0078
        """
        self.ensure_one()
        return report.report_data(
            self.env, self.company_ids.ids, self.date_from, self.date_to, self.period)

    def action_export(self):
        """Give the report as an XLSX file or a PDF report (viewer access or above).

        Requirements: FR-DGQ-0078
        """
        self.ensure_one()
        if self.file_format == 'pdf':
            return self.env.ref(
                'data_governance_quality_center_app.action_report_quality').report_action(self)
        return {
            'type': 'ir.actions.act_url',
            'url': '/data_quality/xlsx/report/%s' % self.id,
            'target': 'self',
        }
