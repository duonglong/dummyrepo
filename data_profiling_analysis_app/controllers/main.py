from odoo import http
from odoo.http import request
from odoo.http.stream import content_disposition

XLSX_MIMETYPE = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'


class DataProfilingExport(http.Controller):

    @http.route('/data_profiling/snapshot/<int:snapshot_id>/xlsx', type='http', auth='user')
    def snapshot_xlsx(self, snapshot_id):
        """Download a report or snapshot as XLSX, read as the user (the Data Profiling group only).

        Requirements: FR-DPA-0026
        """
        snapshot = request.env['dpa.snapshot'].browse(snapshot_id).exists()
        if not snapshot:
            return request.not_found()
        filename, content = snapshot._export_xlsx()
        return request.make_response(content, headers=[
            ('Content-Type', XLSX_MIMETYPE),
            ('Content-Disposition', content_disposition(filename)),
        ])
