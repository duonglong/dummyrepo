from odoo.http import request, route

from odoo.addons.mail.controllers.attachment import AttachmentController
from odoo.addons.web.controllers.binary import Binary


class MailAttachmentUpload(AttachmentController):

    @route()
    def mail_attachment_upload(self, *args, **kwargs):
        """Requirements: FR-AM-0012, FR-AM-0017. The chatter upload is a person's upload: name it by its pattern."""
        request.update_context(am_name_from_file=True)
        return super().mail_attachment_upload(*args, **kwargs)


class BinaryAttachmentUpload(Binary):

    @route()
    def upload_attachment(self, *args, **kwargs):
        """Requirements: FR-AM-0012, FR-AM-0017. A file field upload is a person's upload too."""
        request.update_context(am_name_from_file=True)
        return super().upload_attachment(*args, **kwargs)
