from odoo import api, fields, models
from odoo.exceptions import ValidationError


class DataQualityDuplicateDismissal(models.Model):
    """A pair of records set aside as not duplicates under a rule: later checks leave it out.

    Requirements: FR-DGQ-0039, FR-DGQ-0041
    """
    _name = 'data.quality.duplicate.dismissal'
    _description = 'Data Quality Duplicate Dismissal'

    rule_id = fields.Many2one(
        'data.quality.duplicate.rule', string='Rule', required=True, ondelete='cascade', index=True)
    res_model = fields.Char(string='Model', required=True)
    res_id_a = fields.Integer(string='First Record', required=True)
    res_id_b = fields.Integer(string='Second Record', required=True)
    user_id = fields.Many2one('res.users', string='Set Aside By', ondelete='set null')
    date = fields.Datetime(default=fields.Datetime.now)

    _pair_unique = models.Constraint(
        'UNIQUE(rule_id, res_id_a, res_id_b)', 'This pair is already set aside for the rule.')

    @api.constrains('res_id_a', 'res_id_b')
    def _check_pair_order(self):
        """A pair is stored once, smaller id first.

        Requirements: FR-DGQ-0041
        """
        for dismissal in self:
            if dismissal.res_id_a >= dismissal.res_id_b:
                raise ValidationError(self.env._("A pair holds two records, the smaller id first."))
