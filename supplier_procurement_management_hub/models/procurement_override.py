from odoo import api, fields, models

from ..services import audit, supplier_control


class ProcurementOverride(models.Model):
    """A personal, per-record override of a status or risk refusal.

    Requirements: BR-SPM-0006, FR-SPM-0013
    Only a Procurement manager creates one (access rights); nobody changes or
    deletes it, and each one is written to the audit trail.
    """
    _name = 'procurement.override'
    _description = 'Procurement Override'
    _order = 'date desc, id desc'

    res_model = fields.Char(required=True, readonly=True)
    res_id = fields.Many2oneReference(model_field='res_model', required=True, readonly=True)
    partner_id = fields.Many2one('res.partner', required=True, readonly=True,
                                 ondelete='cascade', index=True)
    action = fields.Selection([
        ('invite', 'Invite'), ('award', 'Award'), ('confirm', 'Confirm'),
    ], required=True, readonly=True)
    kind = fields.Selection([('status', 'Status'), ('risk', 'Risk')],
                            required=True, readonly=True)
    reason = fields.Text(required=True, readonly=True)
    user_id = fields.Many2one('res.users', readonly=True, ondelete='restrict',
                              default=lambda self: self.env.user)
    date = fields.Datetime(readonly=True, default=fields.Datetime.now)
    company_id = fields.Many2one('res.company', required=True, readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        """Record the override and its audit entry.

        Requirements: FR-SPM-0013
        """
        vals_list = [dict(vals, user_id=self.env.user.id) for vals in vals_list]
        overrides = super().create(vals_list)
        for override in overrides:
            audit.log(
                self.env, supplier_control.OVERRIDE_AUDIT_EVENTS[override.kind], override, {},
                {'res_model': override.res_model, 'res_id': override.res_id,
                 'partner_id': override.partner_id.id, 'action': override.action,
                 'kind': override.kind, 'reason': override.reason},
                override.reason)
        return overrides
