from odoo import fields, models


class DuplicateMergeLinkLine(models.TransientModel):
    _name = 'duplicate.merge.link.line'
    _description = 'Merge Preview: Linked Documents'
    _order = 'model_label'

    wizard_id = fields.Many2one(
        'duplicate.merge.wizard', required=True, ondelete='cascade')
    model_label = fields.Char(help="Kind of linked document, for example Sales Order.")
    count = fields.Integer(help="How many documents of this kind move to the kept record.")
