from odoo import _, api, models

DUPLICATE_EVENT = 'attachment_management/duplicate'
DUPLICATE_MAX_NAMES = 5


class IrAttachment(models.Model):  # sdd-allow: model-split the duplicate warning lives apart from the search helpers, as the task lists
    _inherit = 'ir.attachment'

    @api.model_create_multi
    def create(self, vals_list):
        """Requirements: FR-AM-0014, FR-AM-0015.

        The file is always stored; the warning only informs the uploader and is
        sent after the commit, so a failed upload never warns.
        """
        attachments = super().create(vals_list)
        if self._wants_duplicate_check():
            attachments._warn_about_duplicates()
        return attachments

    @api.model
    def _wants_duplicate_check(self):
        """Internal users only, and not while installing, importing or when the caller asked to skip."""
        context = self.env.context
        return (
            self.env.user._is_internal()
            and not context.get('install_mode')
            and not context.get('import_file')
            and not context.get('am_skip_duplicate_check')
        )

    def _warn_about_duplicates(self):
        """Requirements: FR-AM-0014, FR-AM-0016, BR-AM-0003.

        Look for other files with the same content (the checksum, whatever the
        name) and tell the uploader, naming only the copies they can open.
        """
        uploads = self.filtered(
            lambda attachment: attachment.type == 'binary' and attachment.checksum and not attachment.res_field)
        if not uploads:
            return
        domain = [
            ('checksum', 'in', uploads.mapped('checksum')),
            ('type', '=', 'binary'),
            ('res_field', '=', False),
            ('id', 'not in', uploads.ids),
        ]
        # sudo: only whether a copy exists leaves this search; names come from the search made as the uploader below.
        if not self.sudo().search_count(domain, limit=1):
            return
        # with_user drops a sudo of the caller (the chatter upload route creates in sudo).
        readable_copies = self.with_user(self.env.user).search(domain, limit=DUPLICATE_MAX_NAMES, order='id')
        self.env.user._bus_send(DUPLICATE_EVENT, {
            'message': self._duplicate_message(readable_copies),
            'copies': [
                {'name': copy.name, 'res_model': copy.res_model, 'res_id': copy.res_id}
                for copy in readable_copies if copy.res_model and copy.res_id
            ],
        })

    @api.model
    def _duplicate_message(self, copies):
        """Warning text: names the readable copies, or only says the file is already stored."""
        if not copies:
            return _("This file is already stored. Your upload was kept.")
        places = ", ".join(
            "%s (%s)" % (copy.name, copy.record_label) if copy.record_label else copy.name for copy in copies)
        return _("This file is already stored: %s. Your upload was kept.", places)
