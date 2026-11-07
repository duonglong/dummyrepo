import json

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

from ..const import MASK


class Base64Binary(fields.Binary):
    """Binary field that also takes base64 bytes, the legacy upload format."""

    def convert_to_cache(self, value, records, validate=True):
        if isinstance(value, bytes):
            value = value.decode('ascii')
        return super().convert_to_cache(value, records, validate)


class ConfigSnapshotImport(models.TransientModel):
    """Import wizard, steps upload and review: load a file, list differences.

    Requirements: FR-TCM-005, FR-TCM-006, FR-TCM-007, FR-TCM-013, FR-TCM-016,
    BR-TCM-001, BR-TCM-002, NFR-PERF-001, NFR-SEC-001, NFR-USE-001
    """

    _name = 'config.snapshot.import'
    _description = 'Import settings wizard'

    file = Base64Binary(string='File')
    file_name = fields.Char()
    state = fields.Selection(
        [('upload', 'Upload'), ('review', 'Review'), ('confirm', 'Confirm')],
        default='upload', required=True)
    line_ids = fields.One2many('config.snapshot.import.line', 'import_id')
    added_count = fields.Integer(compute='_compute_counts', store=True)
    modified_count = fields.Integer(compute='_compute_counts', store=True)
    removed_count = fields.Integer(compute='_compute_counts', store=True)
    difference_count = fields.Integer(compute='_compute_counts', store=True)

    @api.depends('line_ids.change_type')
    def _compute_counts(self):
        for wizard in self:
            kinds = wizard.line_ids.mapped('change_type')
            wizard.added_count = kinds.count('added')
            wizard.modified_count = kinds.count('modified')
            wizard.removed_count = kinds.count('removed')
            wizard.difference_count = len(kinds)

    def _read_file(self):
        """Return the settings of the uploaded file as a dict key -> entry.

        Requirements: FR-TCM-005
        """
        self.ensure_one()
        try:
            data = json.loads((self.file.content if self.file else b'').decode('utf-8'))
        except ValueError:  # includes bad base64, bad UTF-8 and bad JSON
            raise UserError(self.env._('The file is not valid JSON.'))
        if (not isinstance(data, dict) or 'format_version' not in data
                or not isinstance(data.get('settings'), list)):
            raise UserError(self.env._('The file is not a configuration export.'))
        entries = {}
        for entry in data['settings']:
            if not isinstance(entry, dict) or not isinstance(entry.get('key'), str):
                raise UserError(self.env._('The file is not a configuration export.'))
            entries[entry['key']] = entry
        return entries

    def _build_lines(self, entries):
        """Compare the file entries with the settings here, in batch.

        Requirements: FR-TCM-006, FR-TCM-007, BR-TCM-001, BR-TCM-002, NFR-PERF-001
        """
        covered = {
            item['parameter_id'][0]: item['sensitive']
            for item in self.env['config.snapshot.item'].sudo().search_read(
                [], ['parameter_id', 'sensitive'])}
        params = {
            param['key']: param
            for param in self.env['ir.config_parameter'].sudo().search_read(
                ['|', ('key', 'in', list(entries)), ('id', 'in', list(covered))],
                ['key', 'value'])}
        lines = []
        for key, entry in entries.items():
            file_sensitive = bool(entry.get('sensitive'))
            value = entry.get('value')
            value = '' if value in (None, False) else str(value)
            param = params.get(key)
            if not param:
                lines.append({
                    'key': key, 'change_type': 'added',
                    'sensitive': file_sensitive, 'file_sensitive': file_sensitive,
                    'file_value': '' if file_sensitive else value})
                continue
            here_sensitive = covered.get(param['id'], False)
            if file_sensitive or here_sensitive or (param['value'] or '') == value:
                continue
            lines.append({
                'key': key, 'change_type': 'modified', 'parameter_id': param['id'],
                'current_value': param['value'] or '', 'file_value': value})
        by_id = {param['id']: param for param in params.values()}
        for param_id, here_sensitive in covered.items():
            param = by_id.get(param_id)
            if param and param['key'] not in entries:
                lines.append({
                    'key': param['key'], 'change_type': 'removed',
                    'parameter_id': param_id, 'sensitive': here_sensitive,
                    'current_value': MASK if here_sensitive else param['value'] or ''})
        return lines

    def action_compare(self):
        """Parse the file, list the differences, log the import; change nothing.

        Requirements: FR-TCM-005, FR-TCM-006, FR-TCM-007, FR-TCM-013, FR-TCM-016
        """
        self.ensure_one()
        if not self.env.user.has_group('base.group_system'):
            raise AccessError(self.env._('Only administrators can import settings.'))
        lines = self._build_lines(self._read_file())
        self.line_ids = [(5, 0, 0)] + [(0, 0, vals) for vals in lines]
        self.state = 'review'
        self.env['config.snapshot.log']._record({
            'log_type': 'import',
            'file_name': self.file_name,
            'added_count': self.added_count,
            'modified_count': self.modified_count,
            'removed_count': self.removed_count,
        })
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }
