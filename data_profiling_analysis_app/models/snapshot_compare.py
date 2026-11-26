from odoo import Command, api, fields, models
from odoo.exceptions import ValidationError

RATE_MEASURES = (
    ('completeness', 'completeness'),
    ('empty_rate', 'empty_rate'),
)
COUNT_MEASURES = (
    ('distinct', 'distinct_count'),
    ('inconsistent', 'inconsistent_count'),
    ('unusual', 'unusual_count'),
)


class DpaSnapshotCompare(models.TransientModel):
    _name = 'dpa.snapshot.compare'
    _description = 'Comparison of two snapshots'

    snapshot_a_id = fields.Many2one(
        'dpa.snapshot', string="Snapshot", required=True, ondelete='cascade', domain=[('is_saved', '=', True)])
    snapshot_b_id = fields.Many2one(
        'dpa.snapshot', string="Compared with", required=True, ondelete='cascade', domain=[('is_saved', '=', True)])
    older_id = fields.Many2one('dpa.snapshot', string="Older", compute='_compute_older_newer')
    newer_id = fields.Many2one('dpa.snapshot', string="Newer", compute='_compute_older_newer')
    older_record_count = fields.Integer(string="Records (older)", related='older_id.record_count')
    newer_record_count = fields.Integer(string="Records (newer)", related='newer_id.record_count')
    older_outdated_count = fields.Integer(string="Outdated (older)", related='older_id.outdated_count')
    newer_outdated_count = fields.Integer(string="Outdated (newer)", related='newer_id.outdated_count')
    line_ids = fields.One2many('dpa.snapshot.compare.line', 'compare_id', string="Fields")

    @api.model
    def default_get(self, fields_list):
        """Prefill A and B from two snapshots selected in the list.

        Requirements: FR-DPA-0023
        """
        values = super().default_get(fields_list)
        if self.env.context.get('active_model') == 'dpa.snapshot':
            active_ids = self.env.context.get('active_ids') or []
            if len(active_ids) == 2:
                values.setdefault('snapshot_a_id', active_ids[0])
                values.setdefault('snapshot_b_id', active_ids[1])
        return values

    @api.depends('snapshot_a_id.run_date', 'snapshot_b_id.run_date')
    def _compute_older_newer(self):
        """Put the older snapshot first whatever the order of picking.

        Requirements: FR-DPA-0023
        """
        for compare in self:
            ordered = (compare.snapshot_a_id | compare.snapshot_b_id).sorted(lambda s: (s.run_date, s.id))
            compare.older_id = ordered[:1]
            compare.newer_id = ordered[1:2]

    @api.onchange('snapshot_a_id', 'snapshot_b_id')
    def _onchange_snapshots(self):
        """Drop the lines of the previous pair and warn when the pair is unlike.

        Requirements: FR-DPA-0023, FR-DPA-0024
        """
        self.line_ids = [Command.clear()]
        first, second = self.snapshot_a_id, self.snapshot_b_id
        if first and second and not self._is_valid_pair(first, second):
            return {'warning': {'title': self.env._("Invalid comparison"), 'message': self._pair_message()}}

    @api.model
    def _pair_message(self):
        return self.env._("Choose two different snapshots of the same model.")

    @api.model
    def _is_valid_pair(self, first, second):
        return (first != second and first.is_saved and second.is_saved
                and first.model_name == second.model_name)

    @api.constrains('snapshot_a_id', 'snapshot_b_id')
    def _check_same_model(self):
        """Refuse a snapshot compared with itself, snapshots of different models and unsaved reports.

        Requirements: FR-DPA-0024
        """
        for compare in self:
            if not self._is_valid_pair(compare.snapshot_a_id, compare.snapshot_b_id):
                raise ValidationError(self._pair_message())

    def action_compare(self):
        """Build one line per field of the two snapshots, older snapshot's order first, and show the result.

        Requirements: FR-DPA-0023, FR-DPA-0024, FR-DPA-0025
        """
        self.ensure_one()
        self._check_same_model()
        older = self.older_id.field_line_ids.grouped('field_name')
        newer = self.newer_id.field_line_ids.grouped('field_name')
        names = list(older) + [name for name in newer if name not in older]
        self.line_ids.unlink()
        self.write({'line_ids': [
            Command.create(self.env['dpa.snapshot.compare.line']._line_values(
                sequence, older.get(name), newer.get(name)))
            for sequence, name in enumerate(names, start=1)]})
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }


class DpaSnapshotCompareLine(models.TransientModel):
    _name = 'dpa.snapshot.compare.line'
    _description = 'Field of a comparison of two snapshots'
    _order = 'compare_id, sequence, id'

    compare_id = fields.Many2one('dpa.snapshot.compare', required=True, ondelete='cascade')
    sequence = fields.Integer()
    field_name = fields.Char()
    field_label = fields.Char(string="Field")
    in_older = fields.Boolean(string="In older")
    in_newer = fields.Boolean(string="In newer")
    older_note = fields.Char(string="Older")
    newer_note = fields.Char(string="Newer")
    older_rated = fields.Boolean(string="Older rated")
    newer_rated = fields.Boolean(string="Newer rated")
    older_completeness = fields.Float(string="Completeness (older)", digits=(16, 1))
    newer_completeness = fields.Float(string="Completeness (newer)", digits=(16, 1))
    completeness_change = fields.Float(string="Completeness change", digits=(16, 1))
    older_empty_rate = fields.Float(string="Empty rate (older)", digits=(16, 1))
    newer_empty_rate = fields.Float(string="Empty rate (newer)", digits=(16, 1))
    empty_rate_change = fields.Float(string="Empty rate change", digits=(16, 1))
    older_distinct = fields.Integer(string="Distinct (older)")
    newer_distinct = fields.Integer(string="Distinct (newer)")
    distinct_change = fields.Integer(string="Distinct change")
    older_inconsistent = fields.Integer(string="Inconsistent (older)")
    newer_inconsistent = fields.Integer(string="Inconsistent (newer)")
    inconsistent_change = fields.Integer(string="Inconsistent change")
    older_unusual = fields.Integer(string="Unusual (older)")
    newer_unusual = fields.Integer(string="Unusual (newer)")
    unusual_change = fields.Integer(string="Unusual change")

    @api.model
    def _line_values(self, sequence, older_line, newer_line):
        """Values of one comparison line; a field in one snapshot only, or a side without records, has no change.

        Requirements: FR-DPA-0023, FR-DPA-0025, FR-DPA-0028
        """
        label = (older_line or newer_line).field_label
        older_rated = bool(older_line and older_line.is_rated)
        newer_rated = bool(newer_line and newer_line.is_rated)
        values = {
            'sequence': sequence,
            'field_name': (older_line or newer_line).field_name,
            'field_label': label,
            'in_older': bool(older_line),
            'in_newer': bool(newer_line),
            'older_rated': older_rated,
            'newer_rated': newer_rated,
            'older_note': self._side_note(older_line, older_rated),
            'newer_note': self._side_note(newer_line, newer_rated),
        }
        for measure, source in COUNT_MEASURES:
            self._add_measure(values, measure, source, older_line, newer_line)
        for measure, source in RATE_MEASURES:
            self._add_measure(
                values, measure, source, older_line if older_rated else None, newer_line if newer_rated else None)
        return values

    @api.model
    def _side_note(self, line, rated):
        """The word shown instead of the values of a side: not profiled, or "-" for a model without records."""
        if not line:
            return self.env._("Not profiled")
        return '' if rated else '-'

    @api.model
    def _add_measure(self, values, measure, source, older_line, newer_line):
        if older_line:
            values['older_' + measure] = older_line[source]
        if newer_line:
            values['newer_' + measure] = newer_line[source]
        if older_line and newer_line:
            values[measure + '_change'] = newer_line[source] - older_line[source]
