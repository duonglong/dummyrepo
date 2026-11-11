from odoo import models
from odoo.exceptions import UserError


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

    def action_open_record(self):
        """Open the record the attachment belongs to.

        Requirements: UX-AM-001, BR-AM-0001
        """
        self.ensure_one()
        self.check_access('read')
        self._am_check_parent_access()
        # sudo: the link to the parent is read after the access checks above.
        res_model, res_id = self.sudo().res_model, self.sudo().res_id
        if not (res_model and res_id):
            raise UserError(self.env._("This file is not attached to a record."))
        return {
            'type': 'ir.actions.act_window',
            'res_model': res_model,
            'res_id': res_id,
            'views': [(False, 'form')],
            'target': 'current',
        }
