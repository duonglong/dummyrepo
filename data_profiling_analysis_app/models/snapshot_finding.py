from odoo import fields, models


class DpaSnapshotFinding(models.Model):
    _name = 'dpa.snapshot.finding'
    _description = 'Flagged value or outdated record'
    _order = 'snapshot_id, id'

    snapshot_id = fields.Many2one('dpa.snapshot', required=True, ondelete='cascade', index=True)
    field_line_id = fields.Many2one('dpa.snapshot.field', ondelete='cascade', index=True)
    kind = fields.Selection(
        [('inconsistent_format', "Inconsistent format"),
         ('unusual_number', "Unusual number"),
         ('outdated', "Outdated")], required=True)
    res_model = fields.Char(required=True)
    res_id = fields.Many2oneReference(model_field='res_model')
    record_name = fields.Char()
    value_label = fields.Char(string="Value")
    last_update = fields.Datetime(string="Last update")
    days_since_update = fields.Integer(string="Days since update")
