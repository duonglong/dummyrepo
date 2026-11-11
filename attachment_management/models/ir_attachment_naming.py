import logging

from odoo import api, models

_logger = logging.getLogger(__name__)

PENDING_UPLOAD_MODEL = 'mail.compose.message'


class IrAttachment(models.Model):  # sdd-allow: model-split the naming hook lives apart from the search helpers, as the task lists
    _inherit = 'ir.attachment'

    @api.model_create_multi
    def create(self, vals_list):
        """Requirements: FR-AM-0012, FR-AM-0013, FR-AM-0017.

        Only a person's upload (context ``am_name_from_file``) is renamed; any
        other path keeps the name it was given, which is how a typed name stays.
        """
        if self.env.context.get('am_name_from_file'):
            vals_list = self._with_pattern_names(vals_list)
        return super().create(vals_list)

    def write(self, vals):
        """Requirements: FR-AM-0012. A pending composer upload is named once, when it lands on its record."""
        if vals.get('res_model') and vals.get('res_id') and vals['res_model'] != PENDING_UPLOAD_MODEL:
            pending = self.filtered(
                lambda attachment: attachment.res_model == PENDING_UPLOAD_MODEL and not attachment.res_id
                and attachment.type == 'binary' and not attachment.res_field)
            if pending:
                names = self._pattern_names([
                    dict(vals, name=attachment.name, mimetype=attachment.mimetype) for attachment in pending])
                for attachment, name in zip(pending, names):
                    if name:
                        super(IrAttachment, attachment).write(dict(vals, name=name))
                    else:
                        super(IrAttachment, attachment).write(vals)
                return super(IrAttachment, self - pending).write(vals)
        return super().write(vals)

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
        except (ValueError, KeyError) as error:
            _logger.warning("Attachment naming skipped, the uploaded name is kept: %s", error)
            return [None] * len(vals_list)
