from odoo import api, fields, models
from odoo.fields import Domain


class DataQualityResultMixin(models.AbstractModel):
    _name = 'data.quality.result.mixin'
    _description = 'Data Quality Result Access'

    res_model = fields.Char(string='Model', required=True, index=True)
    company_id = fields.Many2one('res.company', string='Company', index=True)

    @api.model
    @api.ormcache('self.env.uid')
    def _get_readable_model_names(self):
        """Names of the models the current user may read.

        Requirements: BR-DGQ-0011
        """
        return frozenset(
            name for name, model in self.env.registry.items()
            if model._auto and not model._transient and self.env[name].has_access('read')
        )

    @api.model
    def _access_domain(self, operation):
        """Limit result rows to the models the user may read (superuser and sudo are not filtered).

        A record rule cannot ask which models a user may read, so the restriction is added to the
        access domain, which search, read, write and unlink all go through.

        Requirements: BR-DGQ-0011, NFR-DGQ-0006
        """
        domain = super()._access_domain(operation)
        if operation == 'create':
            return domain
        return domain & Domain('res_model', 'in', sorted(self._get_readable_model_names()))
