from odoo import fields, models


class DataQualityDuplicateGroup(models.Model):
    """A set of likely duplicate records found by a rule, with its highest pair score.

    Requirements: FR-DGQ-0039
    """
    _name = 'data.quality.duplicate.group'
    _inherit = ['data.quality.result.mixin']
    _description = 'Data Quality Duplicate Group'
    _order = 'score desc, id'

    name = fields.Char(help="The name of the first record of the group.")
    rule_id = fields.Many2one(
        'data.quality.duplicate.rule', string='Rule', required=True, ondelete='cascade', index=True)
    check_id = fields.Many2one(
        'data.quality.duplicate.check', string='Check', ondelete='set null')
    state = fields.Selection([
        ('open', 'Open'),
        ('dismissed', 'Not Duplicates'),
        ('merged', 'Merged'),
    ], required=True, default='open', index=True)
    score = fields.Float(string='Highest Score', digits=(5, 1))
    line_ids = fields.One2many('data.quality.duplicate.line', 'group_id', string='Records')
