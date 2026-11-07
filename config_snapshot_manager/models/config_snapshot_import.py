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
    confirmation_text = fields.Char(compute='_compute_confirmation_text')

    @api.depends('line_ids.change_type')
    def _compute_counts(self):
        for wizard in self:
            kinds = wizard.line_ids.mapped('change_type')
            wizard.added_count = kinds.count('added')
            wizard.modified_count = kinds.count('modified')
            wizard.removed_count = kinds.count('removed')
            wizard.difference_count = len(kinds)

    @api.depends('added_count', 'modified_count', 'removed_count')
    def _compute_confirmation_text(self):
        for wizard in self:
            wizard.confirmation_text = self.env._(
                '%(added)s added, %(modified)s modified, %(removed)s removed',
                added=wizard.added_count, modified=wizard.modified_count,
                removed=wizard.removed_count)

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


    def _check_admin(self):
        if not self.env.user.has_group('base.group_system'):
            raise AccessError(self.env._('Only administrators can import settings.'))

    def _form_action(self):
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def action_confirm(self):
        """Offer the confirmation step with the counts; change nothing.

        Requirements: FR-TCM-008, FR-TCM-016
        """
        self.ensure_one()
        self._check_admin()
        if self.state != 'review':
            raise UserError(self.env._('Compare a file first.'))
        self.state = 'confirm'
        return self._form_action()

    def action_cancel(self):
        """Close the wizard; no setting and no log entry changes.

        Requirements: FR-TCM-008
        """
        self.ensure_one()
        self._check_admin()
        return {'type': 'ir.actions.act_window_close'}

    def _check_not_stale(self, params, covered):
        """Refuse when a listed setting changed since the comparison.

        Requirements: FR-TCM-011
        """
        for line in self.line_ids:
            param = params.get(line.key)
            if line.change_type == 'added':
                stale = bool(param)
            else:
                shown = param and (
                    MASK if covered.get(param.id) else param.value or '')
                stale = (not param or param.id != line.parameter_id.id
                         or shown != line.current_value)
            if stale:
                raise UserError(self.env._(
                    'A setting changed after the comparison. Compare again.'))

    def _apply_line(self, line, params, entries):
        """Apply one difference through the ORM; return its change values."""
        Param = self.env['ir.config_parameter'].sudo()
        Item = self.env['config.snapshot.item'].sudo()
        if line.change_type == 'added':
            param = Param.create({'key': line.key, 'value': line.file_value or False})
            Item.create({'parameter_id': param.id, 'sensitive': line.file_sensitive})
            return '', line.file_value, line.file_sensitive
        param = params[line.key]
        if line.change_type == 'modified':
            param.write({'value': line.file_value})
            return line.current_value, line.file_value, False
        old, sensitive = line.current_value, line.sensitive
        param.unlink()
        return old, '', sensitive

    def _cover_file_keys(self, entries):
        """After apply, every setting of the file is covered (ADR-TCM-005)."""
        Item = self.env['config.snapshot.item'].sudo()
        params = self.env['ir.config_parameter'].sudo().search(
            [('key', 'in', list(entries))])
        covered = set(Item.search([('parameter_id', 'in', params.ids)]).parameter_id.ids)
        for param in params:
            if param.id not in covered:
                Item.create({
                    'parameter_id': param.id,
                    'sensitive': bool(entries[param.key].get('sensitive'))})

    def action_apply(self):
        """Apply all differences in one savepoint, then log every change.

        Requirements: FR-TCM-009, FR-TCM-010, FR-TCM-011, FR-TCM-014,
        FR-TCM-016, BR-TCM-002, NFR-REL-001, NFR-SEC-001
        """
        self.ensure_one()
        self._check_admin()
        if self.state != 'confirm':
            raise UserError(self.env._('Confirm the differences first.'))
        changes = []
        with self.env.cr.savepoint():
            params_model = self.env['ir.config_parameter'].sudo()
            params = {p.key: p for p in params_model.search(
                ['|', ('key', 'in', self.line_ids.mapped('key')),
                 ('id', 'in', self.line_ids.parameter_id.ids)])}
            covered = {
                item.parameter_id.id: item.sensitive
                for item in self.env['config.snapshot.item'].sudo().search(
                    [('parameter_id', 'in', [p.id for p in params.values()])])}
            self._check_not_stale(params, covered)
            entries = self._read_file()
            for line in self.line_ids:
                try:
                    old, new, sensitive = self._apply_line(line, params, entries)
                except Exception as error:
                    raise UserError(self.env._(
                        'Applying the setting %(key)s failed: %(error)s',
                        key=line.key, error=error)) from error
                changes.append((line, old, new, sensitive))
            try:
                self._cover_file_keys(entries)
            except Exception as error:
                raise UserError(self.env._(
                    'Covering the settings of the file failed: %(error)s',
                    error=error)) from error
        added, modified, removed = (
            sum(1 for change in changes if change[0].change_type == kind)
            for kind in ('added', 'modified', 'removed'))
        for line, old, new, sensitive in changes:
            self.env['config.snapshot.log']._record({
                'log_type': 'change', 'key': line.key,
                'change_type': line.change_type,
                'old_value': old, 'new_value': new}, sensitive=sensitive)
        self.env.invalidate_all()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'type': 'success',
                'message': self.env._(
                    'Settings applied: %(added)s added, %(modified)s modified, %(removed)s removed.',
                    added=added, modified=modified, removed=removed),
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }
