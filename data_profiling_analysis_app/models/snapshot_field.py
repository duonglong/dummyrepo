from odoo import Command, api, fields, models


class DpaSnapshotField(models.Model):
    _name = 'dpa.snapshot.field'
    _description = 'Profiled field of a report or snapshot'
    _order = 'snapshot_id, sequence, id'

    snapshot_id = fields.Many2one('dpa.snapshot', required=True, ondelete='cascade', index=True)
    sequence = fields.Integer()
    field_id = fields.Many2one('ir.model.fields', string="Field", ondelete='set null')
    field_name = fields.Char(required=True)
    field_label = fields.Char(string="Field")
    field_type = fields.Char(string="Type")
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

    @api.depends('empty_count', 'record_count', 'snapshot_id.empty_threshold')
    def _compute_high_empty(self):
        """Flag the field when its empty values are above the threshold of the report, never at it.

        Requirements: FR-DPA-0005, NFR-USE-0001
        """
        for line in self:
            line.high_empty = line.empty_count * 100 > line.snapshot_id.empty_threshold * line.record_count
            line.flag_labels = self.env._("High empty rate") if line.high_empty else False

    def _line_values(self, line, sequence, field_ids):
        """Values of the field line of one profiled field, as the engine returned it.

        Requirements: FR-DPA-0004, FR-DPA-0007, FR-DPA-0013, FR-DPA-0014, FR-DPA-0015
        """
        field = line['field']
        return {
            'sequence': sequence,
            'field_id': field_ids.get(field.name),
            'field_name': field.name,
            'field_label': field.string,
            'field_type': field.type,
            'record_count': line['record_count'],
            'filled_count': line['filled_count'],
            'empty_count': line['empty_count'],
            'completeness': line['completeness'],
            'empty_rate': line['empty_rate'],
            'distinct_count': line['distinct_count'],
            'more_count': line['more_count'],
            'unused_values': line['unused_values'],
            'value_ids': [
                Command.create({**value, 'sequence': position})
                for position, value in enumerate(line['values'], start=1)
            ],
        }
