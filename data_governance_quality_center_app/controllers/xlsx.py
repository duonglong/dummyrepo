from odoo import http
from odoo.exceptions import AccessError
from odoo.http import request

from ..services import export


class DataQualityXlsx(http.Controller):

    @http.route('/data_quality/xlsx/audit/<int:wizard_id>', type='http', auth='user')
    def audit_xlsx(self, wizard_id):
        """Download the audit entries chosen in an export dialog (administrators only).

        Requirements: FR-DGQ-0074
        """
        wizard = request.env['data.quality.audit.export'].browse(wizard_id).exists()
        if not wizard or not request.env.user.has_group(
                'data_governance_quality_center_app.group_dgq_admin'):
            raise AccessError(request.env._("The audit export is for administrators."))
        content = export.audit_xlsx(wizard._get_entries())
        return request.make_response(content, headers=[
            ('Content-Type', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'),
            ('Content-Disposition', 'attachment; filename="audit_trail.xlsx"'),
        ])
