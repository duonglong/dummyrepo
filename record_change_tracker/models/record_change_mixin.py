from collections import defaultdict

from odoo import _, api, fields, models

HISTORY_PAGE_SIZE = 80  # entries shown on the first page of the history tab (NFR-PERF-001)


class RecordChangeMixin(models.AbstractModel):
    _name = 'record.change.mixin'
    _description = 'Change recording of followed fields'

    change_entry_ids = fields.One2many(
        'record.change.entry', 'res_id', string='Change history',
        compute='_compute_change_entry_ids', readonly=True)
    change_tracking_followed = fields.Boolean(
        string='Change tracking followed', compute='_compute_change_tracking_followed')

    @api.depends_context('uid')
    def _compute_change_tracking_followed(self):
        """Requirements: FR-RCT-002. True when the record type is followed (drives the tab)."""
        followed = bool(self._rct_followed_setting())
        for rec in self:
            rec.change_tracking_followed = followed

    @api.depends_context('uid')
    def _compute_change_entry_ids(self):
        """Requirements: FR-RCT-002, FR-RCT-013, NFR-SEC-001.

        Entries of each record, newest first, readable by the user, without sensitive fields.
        """
        sensitive = self.env['record.change.field'].sudo().search([
            ('tracked_model_id.model_id.model', '=', self._name),
            ('is_sensitive', '=', True)]).field_id.mapped('name')
        domain = [('res_model', '=', self._name), ('res_id', 'in', self.ids),
                  ('field_name', 'not in', sensitive)]
        by_record = defaultdict(list)
        for entry in self.env['record.change.entry'].search(domain):
            if len(by_record[entry.res_id]) < HISTORY_PAGE_SIZE:
                by_record[entry.res_id].append(entry.id)
        for rec in self:
            rec.change_entry_ids = self.env['record.change.entry'].browse(by_record.get(rec.id, []))

    def web_read(self, specification):
        """Requirements: FR-RCT-004, BR-RCT-005.

        Opening a form of a followed record type (it asks for the history tab) stores the visit
        before the history is read, so the New marks compare with the opening before.
        """
        if ('change_entry_ids' in specification and self.ids
                and not self.env.context.get('rct_skip_visit') and self._rct_followed_setting()):
            self.env['record.change.visit']._register_visit(self)
        return super().web_read(specification)

    # The visit is a write: the opening RPC must not run on a read-only cursor.
    web_read._readonly = False

    def web_save(self, vals, specification, next_id=None):
        """Requirements: FR-RCT-004, BR-RCT-005.

        Saving the form is not an opening; with next_id (the pager after an edit) the next
        record's form is returned, which is an opening of that record.
        """
        records = self if next_id else self.with_context(rct_skip_visit=True)
        return super(RecordChangeMixin, records).web_save(vals, specification, next_id=next_id)

    def web_save_multi(self, vals_list, specification):
        """Requirements: FR-RCT-004, BR-RCT-005. Saving the form is not an opening."""
        return super(RecordChangeMixin, self.with_context(rct_skip_visit=True)).web_save_multi(
            vals_list, specification)

    @api.model
    def _rct_followed_setting(self):
        return self.env['record.change.model'].sudo().search([
            ('model_id.model', '=', self._name), ('is_followed', '=', True)], limit=1)

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
