from odoo import fields, models
from odoo.exceptions import UserError


class ProcurementAuditLog(models.Model):
    """The protected governance trail: nobody changes or deletes an entry.

    Requirements: BR-SPM-0018, FR-SPM-0109
    """
    _name = 'procurement.audit.log'
    _description = 'Procurement Audit Entry'
    _order = 'date, id'

    date = fields.Datetime(required=True, index=True, readonly=True,
                           default=fields.Datetime.now)
    user_id = fields.Many2one('res.users', readonly=True, ondelete='restrict', default=lambda self: self.env.user)
    company_id = fields.Many2one('res.company', readonly=True, ondelete='restrict',
                                 help="Empty for a record shared by all companies.")
    event = fields.Selection([
        ('supplier_status', 'Supplier status change'),
        ('restricted_field', 'Restricted supplier field change'),
        ('quotation', 'Quotation submission or revision'),
        ('award', 'Award'),
        ('approval', 'Approval decision'),
        ('delegation', 'Delegation'),
        ('contract', 'Contract activation or revision'),
        ('risk_override', 'Risk override'),
        ('status_override', 'Supplier status override'),
        ('exception', 'Exception decision'),
        ('configuration', 'Configuration change'),
    ], required=True, index=True, readonly=True)
    res_model = fields.Char(required=True, readonly=True)
    res_id = fields.Many2oneReference(model_field='res_model', readonly=True)
    record_name = fields.Char(readonly=True)
    old_values = fields.Json(readonly=True)
    new_values = fields.Json(readonly=True)
    comment = fields.Text(readonly=True)

    _date_event_idx = models.Index('(date, event)')
    _record_idx = models.Index('(res_model, res_id)')

    def write(self, vals):
        """Requirements: BR-SPM-0018, FR-SPM-0109 (refused for everyone)."""
        raise UserError(self.env._("Audit entries cannot be changed."))

    def unlink(self):
        """Requirements: BR-SPM-0018, FR-SPM-0109 (refused for everyone)."""
        raise UserError(self.env._("Audit entries cannot be deleted."))
