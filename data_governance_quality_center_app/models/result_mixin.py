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
        """Names of the models the current user may read, sorted.

        Models that use this mixin are left out: results are never about results, and asking
        their own access domain from inside this one would never end.

        Requirements: BR-DGQ-0011
        """
        user_env = self.env(su=False)
        mixin_class = self.env.registry['data.quality.result.mixin']
        return tuple(sorted(
            name for name, model in self.env.registry.items()
            if model._auto and not model._transient and not issubclass(model, mixin_class)
            and user_env[name].has_access('read')
        ))

    @api.model
    def _access_domain(self, operation):
        """Limit result rows to the allowed companies and to the models the user may read.

        A record rule cannot ask which models a user may read, so both restrictions are added
        to the access domain, which search, read, write and unlink all go through. Rows with
        no company are visible to all; superuser and sudo are not filtered.

        Requirements: BR-DGQ-0011, FR-DGQ-0071, NFR-DGQ-0006
        """
        domain = super()._access_domain(operation)
        if operation == 'create':
            return domain
        return (
            domain
            & Domain('res_model', 'in', self._get_readable_model_names())
            & Domain('company_id', 'in', [False, *self.env.companies.ids])
        )
