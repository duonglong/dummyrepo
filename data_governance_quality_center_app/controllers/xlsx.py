from odoo import http
from odoo.exceptions import AccessError, UserError
from odoo.http import request
from odoo.http.stream import content_disposition

from ..services import export, import_run, profile_compare, report


class DataQualityXlsx(http.Controller):

    @http.route('/data_quality/xlsx/audit/<int:wizard_id>', type='http', auth='user')
    def audit_xlsx(self, wizard_id):
        """Download the audit entries chosen in an export dialog (administrators only).

        Requirements: FR-DGQ-0074
        """
        wizard = request.env['data.quality.audit.export'].browse(wizard_id).exists()
        if not request.env.user.has_group('data_governance_quality_center_app.group_dgq_admin'):
            raise AccessError(request.env._("The audit export is for administrators."))
        if not wizard:
            return request.not_found()
        content = export.audit_xlsx(wizard._get_entries())
        return request.make_response(content, headers=[
            ('Content-Type', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'),
            ('Content-Disposition', 'attachment; filename="audit_trail.xlsx"'),
        ])

    @http.route('/data_quality/import/<int:session_id>/error_report', type='http', auth='user')
    def import_error_report(self, session_id):
        """Download the error report of a checked import file, in the format of the file.

        Requirements: FR-DGQ-0061
        """
        session = request.env['data.quality.import.session'].browse(session_id)
        if not session.exists():
            return request.not_found()
        session.check_access('read')
        try:
            content, name, mimetype = import_run.error_report(session)
        except UserError as refusal:
            return request.not_found(str(refusal))
        return request.make_response(content, headers=[
            ('Content-Type', mimetype),
            ('Content-Disposition', content_disposition(name)),
        ])

    @http.route('/data_quality/xlsx/profile/<int:profile_id>', type='http', auth='user')
    def profile_xlsx(self, profile_id):
        """Download a finished profile, read as the user (same exclusions as the profile).

        Requirements: FR-DGQ-0057
        """
        profile = request.env['data.quality.profile'].browse(profile_id).exists()
        if not profile:
            return request.not_found()
        profile.check_access('read')
        profile._check_exportable()
        return request.make_response(profile_compare.profile_xlsx(profile), headers=[
            ('Content-Type', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'),
            ('Content-Disposition', 'attachment; filename="profile_%s.xlsx"' % profile.res_model),
        ])

    @http.route('/data_quality/xlsx/report/<int:wizard_id>', type='http', auth='user')
    def report_xlsx(self, wizard_id):
        """Download the quality report chosen in a report dialog (viewer access or above).

        Requirements: FR-DGQ-0078
        """
        wizard = request.env['data.quality.report.wizard'].browse(wizard_id).exists()
        if not wizard:
            return request.not_found()
        content = report.report_xlsx(request.env, wizard._report_data())
        return request.make_response(content, headers=[
            ('Content-Type', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'),
            ('Content-Disposition', 'attachment; filename="quality_report.xlsx"'),
        ])
