import os

from werkzeug.exceptions import NotFound

from odoo import http
from odoo.http import request
from odoo.http.stream import content_disposition


class AttachmentZipController(http.Controller):

    @http.route('/attachment_management/zip/<int:download_id>', type='http', auth='user', readonly=False)
    def download_zip(self, download_id):
        """Requirements: FR-AM-0008, FR-AM-0009, FR-AM-0010.

        Check again that the requester can open every file, build the zip when it is not built
        yet and stream it; the request is marked downloaded and the temporary file deleted only
        once the whole file was read, so a broken transfer can be repeated. Only the requester
        finds it.
        """
        download = request.env['attachment.zip.download'].search(
            [('id', '=', download_id), ('user_id', '=', request.env.uid), ('state', '=', 'ready')])
        if not download:
            raise NotFound()
        # sudo: only administrators read the path of the temporary file; the requester owns the request.
        if download.sudo().file_path:
            download._check_files_access()
        else:
            download._build()
        path = download.sudo().file_path
        try:
            size = os.path.getsize(path)
        except FileNotFoundError:
            raise NotFound()
        response = request.make_response(download.sudo()._stream_then_release(path), headers=[
            ('Content-Type', 'application/zip'),
            ('Content-Length', size),
            ('Content-Disposition', content_disposition(download.zip_name)),
            ('X-Content-Type-Options', 'nosniff'),
        ])
        return response
