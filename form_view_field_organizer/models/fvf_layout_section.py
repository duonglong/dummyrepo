from odoo import api, fields, models


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

    @api.model_create_multi
    def create(self, vals_list: list[dict]) -> 'FvfLayoutSection':
        """Requirements: FR-FVF-0014 (a published layout refuses new sections)"""
        self.env['fvf.layout'].browse([vals['layout_id'] for vals in vals_list if vals.get('layout_id')])._fvf_check_unlocked()
        return super().create(vals_list)

    def write(self, vals: dict) -> bool:
        """Requirements: FR-FVF-0014"""
        self.layout_id._fvf_check_unlocked()
        return super().write(vals)

    def unlink(self) -> bool:
        """Requirements: FR-FVF-0014"""
        self.layout_id._fvf_check_unlocked()
        return super().unlink()
