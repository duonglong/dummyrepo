import base64
import json

from odoo import api, fields, models

from ..const import MASK

FORMAT_VERSION = 1


class ConfigSnapshotExport(models.TransientModel):
    """The JSON file of one export, kept until the browser has downloaded it.

    Requirements: BR-TCM-001, BR-TCM-002, FR-TCM-003, FR-TCM-004, FR-TCM-012,
    FR-TCM-016, NFR-SEC-001
    (access: ACL of base.group_system only)
    """

    _name = 'config.snapshot.export'
    _description = 'Config snapshot export'

    # base64 text of the JSON, as the acceptance tests read it; Odoo 20 Binary
    # values hold raw bytes, so the browser download is the computed field below
    file = fields.Text(readonly=True)
    download = fields.Binary(compute='_compute_download', attachment=False)
    file_name = fields.Char()
    setting_count = fields.Integer(readonly=True)

    @api.depends('file')
    def _compute_download(self):
        """Requirements: FR-TCM-003"""
        for wiz in self:
            wiz.download = wiz.file or False

    def action_export(self):
        """Build the JSON file from the covered list, log it, return the download form.

        Requirements: BR-TCM-001, BR-TCM-002, FR-TCM-003, FR-TCM-004, FR-TCM-012,
        FR-TCM-016, NFR-SEC-001
        """
        self.ensure_one()
        today = fields.Date.context_today(self)
        items = self.env['config.snapshot.item'].search([], order='key')
        settings = [{
            'key': item.key,
            'value': MASK if item.sensitive else item.parameter_id.sudo().value,
            'sensitive': item.sensitive,
        } for item in items]
        payload = {
            'format_version': FORMAT_VERSION,
            'exported_on': fields.Datetime.to_string(fields.Datetime.now()),
            'settings': settings,
        }
        file_name = '%s_config_%s.json' % (self.env.cr.dbname, today)
        self.write({
            'file': base64.b64encode(json.dumps(payload, indent=2).encode('utf-8')).decode('ascii'),
            'file_name': file_name,
            'setting_count': len(settings),
        })
        self.env['config.snapshot.log']._record({
            'log_type': 'export',
            'file_name': file_name,
            'setting_count': len(settings),
        })
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }
