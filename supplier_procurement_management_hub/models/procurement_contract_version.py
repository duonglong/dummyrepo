from odoo import api, fields, models
from odoo.exceptions import UserError

from ..services import contract as contract_service


class ProcurementContractVersion(models.Model):
    """A frozen earlier version of a contract's terms and lines.

    Requirements: FR-SPM-0065
    """
    _name = 'procurement.contract.version'
    _description = 'Purchase Contract Version'
    _order = 'contract_id, version desc'

    contract_id = fields.Many2one(
        'procurement.contract', required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one(
        related='contract_id.company_id', store=True, index=True)
    version = fields.Integer(required=True)
    kind = fields.Selection([('revision', 'Revision'), ('renewal', 'Renewal')], default='revision', required=True)
    date = fields.Datetime(default=fields.Datetime.now, required=True)
    author_id = fields.Many2one('res.users', required=True, ondelete='restrict')
    approved_by_id = fields.Many2one('res.users', ondelete='restrict')
    date_start = fields.Date()
    date_end = fields.Date()
    terms = fields.Json()
    diff_html = fields.Html(
        string="Changes to the next version", compute='_compute_diff_html', sanitize=False)

    _version_unique = models.Constraint(
        'UNIQUE(contract_id, version)', 'A contract has one record per version number.')

    @api.depends('terms', 'contract_id.version', 'contract_id.version_ids.terms')
    def _compute_diff_html(self):
        """Show what the next version changed, or the current terms when this is the last one.

        Requirements: FR-SPM-0065
        """
        for version in self:
            later = version.contract_id.version_ids.filtered(
                lambda other: other.version > version.version).sorted('version')[:1]
            following = later.terms if later else contract_service.build_terms(version.contract_id)
            version.diff_html = contract_service.diff_html(self.env, version.terms or {}, following or {})

    def write(self, vals):
        """Refuse every change: an earlier version is read-only.

        Requirements: FR-SPM-0065
        """
        raise UserError(self.env._("An earlier version of a contract cannot be changed."))

    def unlink(self):
        """Refuse the deletion of an earlier version.

        Requirements: FR-SPM-0065
        """
        raise UserError(self.env._("An earlier version of a contract cannot be deleted."))
