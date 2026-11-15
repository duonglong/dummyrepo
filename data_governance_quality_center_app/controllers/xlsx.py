from odoo import http
from odoo.exceptions import AccessError
from odoo.http import request

from ..services import export, import_run


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
        session.check_access('read')
        if not session.exists():
            return request.not_found()
        content, name, mimetype = import_run.error_report(session)
        return request.make_response(content, headers=[
            ('Content-Type', mimetype),
            ('Content-Disposition', http.content_disposition(name)),
        ])
