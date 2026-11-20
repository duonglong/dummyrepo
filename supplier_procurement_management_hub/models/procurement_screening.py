from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError


class ProcurementScreening(models.Model):
    """A screening outcome (sanctions, credit, compliance) recorded by a person.

    Requirements: FR-SPM-0089, BR-SPM-0020
    The app calls no screening service: every outcome is shown as manually
    recorded, with who and when, and is never changed or deleted afterwards.
    """
    _name = 'procurement.screening'
    _description = 'Supplier Screening'
    _order = 'date desc, id desc'

    partner_id = fields.Many2one(
        'res.partner', string="Supplier", required=True, ondelete='cascade', index=True)
    assessment_id = fields.Many2one(
        'procurement.risk.assessment', string="Risk assessment", ondelete='set null')
    kind = fields.Selection([
        ('sanctions', 'Sanctions'), ('credit', 'Credit'), ('compliance', 'Compliance'), ('other', 'Other'),
    ], required=True, default='sanctions')
    source = fields.Char(required=True, help="The list or service the person checked.")
    result = fields.Selection([
        ('clear', 'No match'), ('match', 'Match'), ('inconclusive', 'Inconclusive'),
    ], required=True)
    date = fields.Date(required=True, default=fields.Date.context_today)
    user_id = fields.Many2one('res.users', string="Recorded by", readonly=True, ondelete='restrict',
                              default=lambda self: self.env.user)
    origin = fields.Selection([('manual', 'Manually recorded'), ('integration', 'Integration')],
                              required=True, readonly=True, default='manual')
    note = fields.Text()

    @api.constrains('partner_id', 'assessment_id')
    def _check_assessment_partner(self):
        """Requirements: FR-SPM-0089"""
        for screening in self:
            if screening.assessment_id and screening.assessment_id.partner_id != screening.partner_id:
                raise ValidationError(self.env._("The risk assessment belongs to another supplier."))

    @api.depends('source', 'result', 'note', 'date', 'user_id', 'origin')
    def _compute_display_name(self):
        """Requirements: BR-SPM-0020

        The outcome and who recorded it, never a claim of verification.
        """
        results = dict(self._fields['result']._description_selection(self.env))
        for screening in self:
            outcome = (screening.note or '').strip() or results.get(screening.result, '')
            if screening.origin == 'integration':
                screening.display_name = self.env._(
                    "%(list)s: %(outcome)s, received from the configured integration on %(date)s",
                    list=screening.source, outcome=outcome, date=fields.Date.to_string(screening.date))
            else:
                screening.display_name = self.env._(
                    "%(list)s: %(outcome)s, manually recorded by %(user)s on %(date)s",
                    list=screening.source, outcome=outcome, user=screening.user_id.name,
                    date=fields.Date.to_string(screening.date))

    @api.model_create_multi
    def create(self, vals_list):
        """Requirements: FR-SPM-0089, BR-SPM-0020

        A user's outcome is always manual and carries the user: the client sets neither.
        """
        if not self.env.su:
            vals_list = [dict(vals, origin='manual', user_id=self.env.user.id) for vals in vals_list]
        return super().create(vals_list)

    def write(self, vals):
        """Requirements: BR-SPM-0020 (an outcome is never changed)."""
        if not self.env.su:
            raise UserError(self.env._("A recorded screening cannot be changed: record a new one."))
        return super().write(vals)

    def unlink(self):
        """Requirements: BR-SPM-0020 (an outcome is never deleted)."""
        if not self.env.su:
            raise UserError(self.env._("A recorded screening cannot be deleted."))
        return super().unlink()
