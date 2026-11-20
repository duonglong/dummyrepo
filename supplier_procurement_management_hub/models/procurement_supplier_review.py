from odoo import api, fields, models
from odoo.exceptions import ValidationError

from ..services import performance

OPEN_REVIEW_STATES = ('to_plan', 'planned')


class ProcurementSupplierReview(models.Model):
    """A review meeting, incident or complaint with its participants, findings and corrective actions.

    Requirements: FR-SPM-0086, FR-SPM-0087
    """
    _name = 'procurement.supplier.review'
    _description = 'Supplier Review'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date desc, id desc'
    _check_company_auto = True

    partner_id = fields.Many2one('res.partner', string="Supplier", required=True, ondelete='cascade', index=True)
    kind = fields.Selection([
        ('review', 'Review meeting'), ('incident', 'Incident'), ('complaint', 'Complaint'),
    ], required=True, default='review')
    date = fields.Date()
    participant_ids = fields.Many2many(
        'res.users', 'spm_supplier_review_participant_rel', 'review_id', 'user_id', string="Participants")
    findings = fields.Html()
    rejected_qty = fields.Float(string="Rejected quantity", help="Goods rejected because of the incident or complaint.")
    state = fields.Selection([
        ('to_plan', 'To plan'), ('planned', 'Planned'), ('done', 'Done'), ('cancelled', 'Cancelled'),
    ], required=True, default='to_plan', tracking=True)
    trigger = fields.Selection([('manual', 'Manual'), ('threshold', 'Score threshold')], default='manual')
    scorecard_id = fields.Many2one('procurement.supplier.scorecard', string="Scorecard", ondelete='set null',
                                   check_company=True)
    responsible_id = fields.Many2one('res.users', string="Responsible", ondelete='set null')
    action_ids = fields.One2many('procurement.corrective.action', 'review_id', string="Corrective actions")
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company, index=True)

    @api.constrains('date', 'state')
    def _check_date(self):
        """A review that is planned or held has a date."""
        for review in self:
            if review.state in ('planned', 'done') and not review.date:
                raise ValidationError(self.env._("A planned or held review needs a date."))

    @api.model
    def _spm_review_on_threshold(self, partner):
        """Create one review to plan for each company where a fresh open scorecard is below its threshold.

        Requirements: FR-SPM-0087
        An open review of the supplier in the same company stops a second one.
        """
        cards = self.env['procurement.supplier.scorecard'].search([
            ('partner_id', '=', partner.id), ('period_closed', '=', False),
            ('template_id', 'in', performance.templates_for(self.env, partner).ids)], order='id')
        created = self.browse()
        for card in cards:
            breaches = performance.threshold_breaches(card)
            if not breaches or self.search_count([
                    ('partner_id', '=', partner.id), ('company_id', '=', card.company_id.id),
                    ('state', 'in', OPEN_REVIEW_STATES)]):
                continue
            review = self.create({
                'partner_id': partner.id, 'company_id': card.company_id.id, 'kind': 'review',
                'state': 'to_plan', 'trigger': 'threshold', 'scorecard_id': card.id,
                'responsible_id': partner.spm_responsible_user_id.id,
                'findings': "<ul>%s</ul>" % "".join("<li>%s</li>" % text for text in breaches),
            })
            if review.responsible_id:
                review.activity_schedule(
                    'mail.mail_activity_data_todo', user_id=review.responsible_id.id,
                    summary=self.env._("Plan a review of %s", partner.display_name))
            created |= review
        return created


class ProcurementJob(models.Model):  # sdd-allow: model-split the job runner is extended per code by the task that owns the job (procurement_job.py, not a file of this task)
    """After the scorecards of a supplier are computed, a low one opens a review to plan.

    Requirements: FR-SPM-0087
    """
    _inherit = 'procurement.job'

    def _spm_process_scorecard(self, partner):
        """Requirements: FR-SPM-0087"""
        result = super()._spm_process_scorecard(partner)
        self.env['procurement.supplier.review']._spm_review_on_threshold(partner)
        return result
