from odoo import fields, models


class FvfLayoutSection(models.Model):
    _name = 'fvf.layout.section'
    _description = 'Form layout section'
    _order = 'sequence, id'

    layout_id = fields.Many2one(
        'fvf.layout', string='Layout', required=True, ondelete='cascade', index=True)
    name = fields.Char(required=True, translate=True)
    sequence = fields.Integer(default=10)
    start_state = fields.Selection(
        [('open', 'Open'), ('closed', 'Closed')], string='Starts', required=True, default='open')
    line_ids = fields.One2many('fvf.layout.line', 'section_id', string='Fields')
