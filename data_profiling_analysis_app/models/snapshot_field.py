from odoo import fields, models


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
    value_ids = fields.One2many('dpa.snapshot.value', 'field_line_id', string="Values")
    finding_ids = fields.One2many('dpa.snapshot.finding', 'field_line_id', string="Findings")

    _snapshot_field_unique = models.Constraint(
        'unique (snapshot_id, field_name)', "A field is profiled once per report.")

    def _line_values(self, line, sequence, field_ids):
        """Values of the field line of one profiled field, as the engine returned it.

        Requirements: FR-DPA-0004
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
        }
