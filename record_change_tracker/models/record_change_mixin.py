from odoo import _, api, fields, models


class RecordChangeMixin(models.AbstractModel):
    _name = 'record.change.mixin'
    _description = 'Change recording of followed fields'

    @api.model
    def _rct_tracker_lines(self):
        """Field lines of this record type's setting, empty when the type is not followed.

        Requirements: FR-RCT-015
        """
        setting = self.env['record.change.model'].sudo().search([
            ('model_id.model', '=', self._name), ('is_followed', '=', True)], limit=1)
        return setting.field_ids

    @api.model
    def _track_get_fields(self):
        """Requirements: FR-RCT-012, BR-RCT-004. Sensitive fields also leave the chatter log."""
        names = super()._track_get_fields()
        sensitive = self.env['record.change.field'].sudo().search([
            ('tracked_model_id.model_id.model', '=', self._name), ('is_sensitive', '=', True)])
        return names - set(sensitive.field_id.mapped('name'))

    def _rct_display(self, fname, value):
        """Readable text of a value: labels and names, blank when empty."""
        field = self._fields[fname]
        if field.type == 'boolean':
            return _("Yes") if value else _("No")
        if value is False or value is None or value == '' or (
                field.relational and not value):
            return ''
        if field.type == 'selection':
            return dict(field._description_selection(self.env)).get(value, value)
        if field.type == 'many2one':
            return value.display_name
        if field.type == 'many2many':
            return ', '.join(value.mapped('display_name'))
        if field.type in ('date', 'datetime'):
            return fields.Date.to_string(value) if field.type == 'date' \
                else fields.Datetime.to_string(value)
        return str(value)

    def write(self, vals):
        """Requirements: FR-RCT-001, FR-RCT-003, FR-RCT-012, FR-RCT-015.

        Write one entry per changed followed, non-sensitive field of each record.
        """
        if self.env.context.get('tracking_disable'):
            return super().write(vals)
        lines = self._rct_tracker_lines().filtered('is_tracked')
        ir_fields = lines.field_id.filtered(lambda f: f.name in vals and f.name in self._fields)
        if not ir_fields:
            return super().write(vals)
        records = self.sudo().filtered('id')
        before = {
            rec.id: {f.name: rec[f.name] for f in ir_fields} for rec in records}
        texts_before = {
            rec.id: {f.name: rec._rct_display(f.name, before[rec.id][f.name]) for f in ir_fields}
            for rec in records}
        result = super().write(vals)
        entries = []
        for rec in records:
            for ir_field in ir_fields:
                name = ir_field.name
                if rec[name] == before[rec.id][name]:
                    continue
                entries.append({
                    'res_model': self._name,
                    'res_id': rec.id,
                    'field_id': ir_field.id,
                    'field_name': name,
                    'field_label': ir_field.field_description,
                    'old_value': texts_before[rec.id][name],
                    'new_value': rec._rct_display(name, rec[name]),
                    'author_id': self.env.uid,
                })
        if entries:
            self.env['record.change.entry'].sudo().create(entries)
        return result
