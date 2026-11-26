from odoo import Command, api, fields, models

from ..services.profiler import NUMBER_TYPES

STATISTIC_KEYS = (
    'min_value', 'max_value', 'sum_value', 'mean_value', 'median_value', 'stddev_value',
    'earliest_date', 'latest_date', 'span_days',
)


class DpaSnapshotField(models.Model):
    _name = 'dpa.snapshot.field'
    _inherit = 'dpa.snapshot.frozen'
    _description = 'Profiled field of a report or snapshot'
    _order = 'snapshot_id, sequence, id'

    snapshot_id = fields.Many2one('dpa.snapshot', required=True, ondelete='cascade', index=True)
    sequence = fields.Integer()
    field_id = fields.Many2one('ir.model.fields', string="Field", ondelete='set null')
    field_name = fields.Char(required=True)
    field_label = fields.Char(string="Field")
    field_type = fields.Char(string="Type")
    is_rated = fields.Boolean(string="Rated", help="False when the model has no records: rates and statistics show '-'.")
    record_count = fields.Integer(string="Records")
    filled_count = fields.Integer(string="Filled")
    empty_count = fields.Integer(string="Empty")
    completeness = fields.Float(string="Completeness (%)", digits=(16, 1))
    empty_rate = fields.Float(string="Empty rate (%)", digits=(16, 1))
    distinct_count = fields.Integer(string="Distinct")
    more_count = fields.Integer(string="More values", help="Distinct values beyond the ones listed.")
    more_note = fields.Char(string="More values note", compute='_compute_more_note')
    high_empty = fields.Boolean(
        string="High empty rate", compute='_compute_high_empty', store=True,
        help="The share of empty values is above the empty-value threshold of the report.")
    flag_labels = fields.Char(string="Flags", compute='_compute_high_empty', store=True)
    inconsistent_count = fields.Integer(string="Inconsistent values")
    unusual_count = fields.Integer(string="Unusual values")
    main_pattern = fields.Char(string="Most common format")
    main_pattern_share = fields.Float(string="Share of the common format (%)", digits=(16, 1))
    has_common_format = fields.Boolean(string="Has a common format")
    format_note = fields.Char(string="Format note", compute='_compute_notes')
    range_computed = fields.Boolean(string="Usual range worked out")
    range_low = fields.Float(string="Usual range from", digits=(16, 4))
    range_high = fields.Float(string="Usual range to", digits=(16, 4))
    range_note = fields.Char(string="Range note", compute='_compute_notes')
    min_value = fields.Float(string="Minimum", digits=(16, 4))
    max_value = fields.Float(string="Maximum", digits=(16, 4))
    sum_value = fields.Float(string="Sum", digits=(16, 4))
    mean_value = fields.Float(string="Average", digits=(16, 4))
    median_value = fields.Float(string="Median", digits=(16, 4))
    stddev_value = fields.Float(string="Standard deviation", digits=(16, 4))
    earliest_date = fields.Date(string="Earliest")
    latest_date = fields.Date(string="Latest")
    span_days = fields.Integer(string="Span (days)")
    unused_values = fields.Char(string="Unused values")
    value_ids = fields.One2many('dpa.snapshot.value', 'field_line_id', string="Values")
    finding_ids = fields.One2many('dpa.snapshot.finding', 'field_line_id', string="Findings")

    _snapshot_field_unique = models.Constraint(
        'unique (snapshot_id, field_name)', "A field is profiled once per report.")

    @api.depends('more_count')
    def _compute_more_note(self):
        """Text shown after the value list when more distinct values exist than are listed.

        Requirements: FR-DPA-0007
        """
        for line in self:
            line.more_note = self.env._("and %s more values", line.more_count) if line.more_count else False

    @api.depends('field_type', 'has_common_format', 'range_computed')
    def _compute_notes(self):
        """Say why a text field shows no common format and a number field no usual range.

        Requirements: FR-DPA-0009, FR-DPA-0010
        """
        for line in self:
            line.format_note = (
                self.env._("No common format") if line.field_type == 'char' and not line.has_common_format else False)
            line.range_note = (
                self.env._("Too few values to work out a usual range.")
                if line.field_type in NUMBER_TYPES and not line.range_computed else False)

    @api.depends(
        'empty_count', 'record_count', 'snapshot_id.empty_threshold', 'inconsistent_count', 'unusual_count')
    def _compute_high_empty(self):
        """Flag the field with a text label for each finding; an empty rate is high above the threshold, never at it.

        Requirements: FR-DPA-0005, FR-DPA-0009, FR-DPA-0010, NFR-USE-0001
        """
        for line in self:
            line.high_empty = line.empty_count * 100 > line.snapshot_id.empty_threshold * line.record_count
            labels = [
                label for flagged, label in (
                    (line.high_empty, self.env._("High empty rate")),
                    (line.inconsistent_count, self.env._("Inconsistent format")),
                    (line.unusual_count, self.env._("Unusual number")),
                ) if flagged]
            line.flag_labels = ', '.join(labels) or False

    def _line_values(self, line, sequence, field_ids):
        """Values of the field line of one profiled field, as the engine returned it.

        Requirements: FR-DPA-0004, FR-DPA-0007, FR-DPA-0008, FR-DPA-0009, FR-DPA-0010, FR-DPA-0013, FR-DPA-0014, FR-DPA-0015,
        FR-DPA-0016, FR-DPA-0017, FR-DPA-0028
        """
        field = line['field']
        findings = line.get('findings', [])
        return {
            'sequence': sequence,
            'field_id': field_ids.get(field.name),
            'field_name': field.name,
            'field_label': field.string,
            'field_type': field.type,
            'is_rated': bool(line['record_count']),
            'record_count': line['record_count'],
            'filled_count': line['filled_count'],
            'empty_count': line['empty_count'],
            'completeness': line['completeness'],
            'empty_rate': line['empty_rate'],
            'distinct_count': line['distinct_count'],
            'more_count': line['more_count'],
            'unused_values': line['unused_values'],
            'main_pattern': line.get('main_pattern', False),
            'main_pattern_share': line.get('main_pattern_share', 0.0),
            'has_common_format': line.get('has_common_format', False),
            'inconsistent_count': sum(f['kind'] == 'inconsistent_format' for f in findings),
            'range_computed': line.get('range_computed', False),
            'range_low': line.get('range_low', 0.0),
            'range_high': line.get('range_high', 0.0),
            'unusual_count': sum(f['kind'] == 'unusual_number' for f in findings),
            **{key: line[key] for key in STATISTIC_KEYS if key in line},
            'value_ids': [
                Command.create({**value, 'sequence': position})
                for position, value in enumerate(line['values'], start=1)
            ],
        }

    def _frozen_snapshots(self):
        return self.snapshot_id
