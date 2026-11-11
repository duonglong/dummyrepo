from odoo import models


class IrAttachment(models.Model):  # sdd-allow: model-split T-003 plans the form actions in their own file
    _inherit = 'ir.attachment'

    def action_preview(self):
        """Open the attachment in Odoo's own file viewer, without downloading it.

        Requirements: FR-AM-0006
        """
        self.ensure_one()
        self.check_access('read')
        self._am_check_parent_access()
        return {
            'type': 'ir.actions.client',
            'tag': 'attachment_management.preview',
            'params': {'attachment_id': self.id},
        }

    def action_download(self):
        """Download the attachment's file.

        Requirements: FR-AM-0007
        """
        self.ensure_one()
        self.check_access('read')
        self._am_check_parent_access()
        return {
            'type': 'ir.actions.act_url',
            'url': '/web/content/%d?download=true' % self.id,
            'target': 'self',
        }
