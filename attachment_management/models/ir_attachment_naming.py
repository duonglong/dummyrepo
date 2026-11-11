import logging

from odoo import api, fields, models
from odoo.exceptions import MissingError

_logger = logging.getLogger(__name__)

PENDING_UPLOAD_MODEL = 'mail.compose.message'


class IrAttachment(models.Model):  # sdd-allow: model-split the naming hook lives apart from the search helpers, as the task lists
    _inherit = 'ir.attachment'

    am_name_pending = fields.Boolean(
        string="Awaiting Its Pattern Name", copy=False, index='btree_not_null',
        help="A file a person uploaded to the message composer, named once when it lands on its record.")

    @api.model_create_multi
    def create(self, vals_list):
        """Requirements: FR-AM-0012, FR-AM-0013, FR-AM-0017.

        Only a person's upload (context ``am_name_from_file``) is renamed; any
        other path keeps the name it was given, which is how a typed name stays.
        """
        if self.env.context.get('am_name_from_file'):
            vals_list = self._with_pattern_names(vals_list)
            vals_list = [
                dict(vals, am_name_pending=True) if self._is_pending_upload(vals) else vals for vals in vals_list]
        return super().create(vals_list)

    def write(self, vals):
        """Requirements: FR-AM-0012. A composer file the upload route made is named once, when it lands on its record.

        Files created without the upload flag (a template's printed report) are
        never marked, so they keep their own name.
        """
        if not (vals.get('res_model') and vals.get('res_id') and vals['res_model'] != PENDING_UPLOAD_MODEL):
            return super().write(vals)
        pending = self.filtered('am_name_pending')
        if not pending:
            return super().write(vals)
        names = self._pattern_names([
            dict(vals, name=attachment.name, mimetype=attachment.mimetype) for attachment in pending])
        for attachment, name in zip(pending, names):
            super(IrAttachment, attachment).write(
                dict(vals, am_name_pending=False, **({'name': name} if name else {})))
        return super(IrAttachment, self - pending).write(vals)

    @api.model
    def _is_pending_upload(self, vals):
        """True for a binary file uploaded to the composer before its message exists."""
        return (vals.get('res_model') == PENDING_UPLOAD_MODEL and not vals.get('res_id')
                and vals.get('type', 'binary') == 'binary' and not vals.get('res_field'))

    @api.model
    def _with_pattern_names(self, vals_list):
        """Return the create values with the pattern name set where a pattern applies."""
        candidates = [vals.get('type', 'binary') == 'binary' for vals in vals_list]
        names = self._pattern_names([vals if is_binary else {} for vals, is_binary in zip(vals_list, candidates)])
        return [dict(vals, name=name) if name else vals for vals, name in zip(vals_list, names)]

    @api.model
    def _pattern_names(self, vals_list):
        try:
            return self.env['attachment.naming.rule']._apply_to(vals_list)
        except (ValueError, MissingError) as error:
            _logger.warning("Attachment naming skipped, the uploaded name is kept: %s", error)
            return [None] * len(vals_list)
