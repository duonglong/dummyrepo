from odoo import fields, models


class DataQualityDuplicateGroupLine(models.Model):
    """One record of a duplicate group with its score against the first record of the group.

    Requirements: FR-DGQ-0039
    """
    _name = 'data.quality.duplicate.line'
    _inherit = ['data.quality.result.mixin']
    _description = 'Data Quality Duplicate Group Record'
    _order = 'sequence, id'

    group_id = fields.Many2one(
        'data.quality.duplicate.group', string='Group', required=True, ondelete='cascade', index=True)
    res_model = fields.Char(related='group_id.res_model', store=True, readonly=True, required=False)
    company_id = fields.Many2one(related='group_id.company_id', store=True, readonly=True)
    res_id = fields.Many2oneReference(string='Record', model_field='res_model', required=True)
    record_name = fields.Char()
    score = fields.Float(string='Score', digits=(5, 1))
    link_count = fields.Integer(
        string='Linked Documents', readonly=True,
        help="How many documents point at this record, for example orders or invoices. "
             "The record with the most is usually the one to keep.")
    sequence = fields.Integer(default=10)

    _record_unique = models.Constraint(
        'UNIQUE(group_id, res_id)', 'A record is in a group once.')

    def action_dismiss(self):
        """Set this record aside as not a duplicate of the others of its group.

        The record leaves the group and is not grouped again with any of them under the rule;
        a group left with fewer than 2 records is closed.

        Requirements: FR-DGQ-0041
        """
        for group in self.group_id:
            group._dismiss_records(self.filtered(lambda line: line.group_id == group))
        return True
