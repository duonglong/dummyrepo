from odoo import fields, models


class DpaSnapshotValue(models.Model):
    _name = 'dpa.snapshot.value'
    _description = 'Distinct value of a profiled field'
    _order = 'field_line_id, sequence, id'

    field_line_id = fields.Many2one('dpa.snapshot.field', required=True, ondelete='cascade', index=True)
    sequence = fields.Integer()
    value_label = fields.Char(string="Value")
    is_empty = fields.Boolean(string="Empty")
    count = fields.Integer(string="Records")
    share = fields.Float(string="Share (%)", digits=(16, 1))
