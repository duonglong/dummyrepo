from odoo import fields, models
from odoo.exceptions import UserError


class DataQualityRuleVersion(models.Model):
    """The frozen previous definition of a rule, with who changed it and what changed.

    Rows are written by the rule itself when its definition changes and are read-only for everyone.

    Requirements: FR-DGQ-0011
    """
    _name = 'data.quality.rule.version'
    _description = 'Data Quality Rule Version'
    _order = 'rule_id, version desc'

    rule_id = fields.Many2one(
        'data.quality.rule', string='Rule', required=True, ondelete='cascade', index=True,
        readonly=True)
    version = fields.Integer(string='Version', required=True, readonly=True)
    definition = fields.Json(
        string='Definition', readonly=True,
        help="What the rule checked in this version: model, check type, fields, settings, "
             "filters, severity and companies.")
    changed_values = fields.Json(
        string='Changed Values', readonly=True,
        help="Each value the change replaced, with the value before and after.")
    user_id = fields.Many2one('res.users', string='Changed By', ondelete='set null', readonly=True)
    date = fields.Datetime(
        string='Changed On', required=True, default=fields.Datetime.now, readonly=True)

    _rule_version_unique = models.Constraint(
        'UNIQUE(rule_id, version)', 'A rule has only one record of each version.')

    def write(self, vals):
        """Refuse every change: a kept version is read-only.

        Requirements: FR-DGQ-0011
        """
        raise UserError(self.env._("A kept version of a rule cannot be changed."))

    def unlink(self):
        """Refuse every deletion: a kept version goes only with its rule (by the database cascade).

        Requirements: FR-DGQ-0011
        """
        raise UserError(self.env._("A kept version of a rule cannot be deleted."))
