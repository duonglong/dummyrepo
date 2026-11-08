from odoo import fields, models


class DuplicateMergeValueLine(models.TransientModel):
    _name = 'duplicate.merge.value.line'
    _description = 'Merge Preview: Field Value'
    _order = 'id'

    wizard_id = fields.Many2one(
        'duplicate.merge.wizard', required=True, ondelete='cascade')
    field_name = fields.Char(required=True)
    field_label = fields.Char()
    kept_value = fields.Char(help="The value the kept record has after the merge.")
    source_name = fields.Char(help="The record this value comes from.")
    is_filled = fields.Boolean(help="The kept record has no value here: it takes the value of a duplicate.")
    write_value = fields.Json(help="The raw value written on the kept record when it is filled.")
