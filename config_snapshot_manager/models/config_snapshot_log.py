from odoo import api, fields, models
from odoo.exceptions import UserError

from ..const import MASK


class ConfigSnapshotLog(models.Model):
    """Append-only audit log of exports, imports and applied changes.

    Requirements: FR-TCM-015, FR-TCM-016, NFR-SEC-001
    """

    _name = 'config.snapshot.log'
    _description = 'Config snapshot audit log'
    _order = 'date desc, id desc'

    log_type = fields.Selection(
        [('export', 'Export'), ('import', 'Import'), ('change', 'Change')],
        required=True, readonly=True, index=True)
    user_id = fields.Many2one(
        'res.users', string='User', required=True, readonly=True,
        ondelete='restrict')
    date = fields.Datetime(
        default=fields.Datetime.now, readonly=True, index=True)
    file_name = fields.Char()
    setting_count = fields.Integer()
    added_count = fields.Integer()
    modified_count = fields.Integer()
    removed_count = fields.Integer()
    key = fields.Char(index=True)
    change_type = fields.Selection(
        [('added', 'Added'), ('modified', 'Modified'), ('removed', 'Removed')])
    old_value = fields.Text()
    new_value = fields.Text()

    @api.model
    def _record(self, vals, sensitive=False):
        """Append one entry as the current user; mask sensitive values.

        Requirements: FR-TCM-015, NFR-SEC-001
        """
        vals = dict(vals, user_id=self.env.uid)
        if sensitive:
            vals.update(old_value=MASK, new_value=MASK)
        # sudo: users have no create right; the tool alone writes entries
        return self.sudo().create(vals)

    def write(self, vals):
        """Requirements: FR-TCM-015"""
        raise UserError(self.env._('Audit log entries cannot be changed or deleted.'))

    def unlink(self):
        """Requirements: FR-TCM-015"""
        raise UserError(self.env._('Audit log entries cannot be changed or deleted.'))
