import os

from werkzeug.exceptions import NotFound
from werkzeug.wsgi import wrap_file

from odoo import http
from odoo.http import request
from odoo.http.stream import content_disposition


class AttachmentZipController(http.Controller):

    @http.route('/attachment_management/zip/<int:download_id>', type='http', auth='user', readonly=False)
    def download_zip(self, download_id):
        """Requirements: FR-AM-0008, FR-AM-0009, FR-AM-0010.

        Build the zip when it is not built yet (access is checked again), stream it
        and delete its temporary file at once; only the requester finds it, once.
        """
        download = request.env['attachment.zip.download'].search(
            [('id', '=', download_id), ('user_id', '=', request.env.uid), ('state', '=', 'ready')])
        if not download:
            raise NotFound()
        # sudo: only administrators read the path of the temporary file; the requester owns the request.
        if not download.sudo().file_path:
            download._build()
        path = download.sudo().file_path
        # sudo: requesters cannot write their requests; the state is kept by the server.
        download.sudo().write({'state': 'downloaded'})
        try:
            handle = open(path, 'rb')
        except FileNotFoundError:
            raise NotFound()
        # The open file stays readable after the unlink: nothing is left on the server.
        os.unlink(path)
        size = os.fstat(handle.fileno()).st_size
        return request.make_response(wrap_file(request.httprequest.environ, handle), headers=[
            ('Content-Type', 'application/zip'),
            ('Content-Length', size),
            ('Content-Disposition', content_disposition(download.zip_name)),
            ('X-Content-Type-Options', 'nosniff'),
        ])
