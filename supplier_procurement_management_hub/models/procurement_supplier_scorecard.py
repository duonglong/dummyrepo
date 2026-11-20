from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

from ..services import performance

MANAGER_GROUP = 'supplier_procurement_management_hub.group_spm_manager'


class ProcurementSupplierScorecard(models.Model):
    """A dated snapshot of a supplier's metrics for one template, company and period.

    Requirements: FR-SPM-0083, FR-SPM-0084, NFR-SPM-0006
    Only the scorecard job and the manager's Recompute write it; screens read the snapshot.
    """
    _name = 'procurement.supplier.scorecard'
    _description = 'Supplier Scorecard'
    _order = 'period_start desc, id'

    partner_id = fields.Many2one('res.partner', string="Supplier", required=True, ondelete='cascade', index=True)
    template_id = fields.Many2one('procurement.scorecard.template', required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one('res.company', required=True, index=True)
    period_start = fields.Date(required=True, index=True)
    period_end = fields.Date(required=True)
    period_closed = fields.Boolean(default=False)
    score = fields.Float()
    range = fields.Selection([('good', 'Good'), ('acceptable', 'Acceptable'), ('poor', 'Poor')])
    computed_at = fields.Datetime(string="Computed at")
    metric_ids = fields.One2many('procurement.supplier.metric', 'scorecard_id', string="Metrics")
    missing_metrics = fields.Text(string="Metrics without data")

    _scorecard_key = models.Constraint(
        'UNIQUE(partner_id, template_id, company_id, period_start)',
        'A supplier has one scorecard per template and period.')

    @api.model
    def _spm_snapshot(self, template, partner, day):
        """Create or replace the open scorecard of the period holding ``day``; closed ones are kept.

        Requirements: FR-SPM-0083
        """
        values = performance.snapshot_values(template, partner, day)
        values['computed_at'] = fields.Datetime.now()
        card = self.search([
            ('partner_id', '=', partner.id), ('template_id', '=', template.id),
            ('company_id', '=', template.company_id.id), ('period_start', '=', values['period_start'])])
        if not card:
            return self.create(values)
        if card.period_closed:
            return card
        card.metric_ids.unlink()
        card.write(values)
        return card

    @api.model
    def _spm_close_ended(self, partner, today):
        """Compute the open scorecards of the supplier whose period ended over their whole length, then close them.

        Requirements: FR-SPM-0083
        """
        ended = self.search([('partner_id', '=', partner.id), ('period_closed', '=', False),
                             ('period_end', '<', today)])
        for card in ended:
            _start, end = performance.period_bounds(card.template_id.period, card.period_start)
            if end == card.period_end:
                self._spm_snapshot(card.template_id, partner, card.period_start)
        ended.write({'period_closed': True})

    def spm_trend(self, metric):
        """Return the same metric of the earlier scorecards of the supplier and template, oldest first.

        Requirements: FR-SPM-0084
        """
        self.ensure_one()
        earlier = self.search([
            ('partner_id', '=', self.partner_id.id), ('template_id', '=', self.template_id.id),
            ('company_id', '=', self.company_id.id), ('period_start', '<', self.period_start)],
            order='period_start')
        return [{'period_start': card.period_start, 'value': row.value, 'has_data': row.has_data}
                for card in earlier for row in card.metric_ids if row.metric == metric]

    def action_spm_recompute(self):
        """Recompute this open scorecard now (a Procurement manager asks).

        Requirements: FR-SPM-0083
        """
        for card in self:
            card.check_access('read')
            if not self.env.user.has_group(MANAGER_GROUP):
                raise AccessError(self.env._("Only a Procurement manager can recompute a scorecard."))
            if card.period_closed:
                raise UserError(self.env._("The period of this scorecard is closed: its snapshot is kept."))
            # elevated: the manager reads every record behind the metrics but may not write snapshots
            card = card.sudo()
            card._spm_snapshot(card.template_id, card.partner_id, card.period_start)
        return True


class ProcurementJob(models.Model):  # sdd-allow: model-split the job runner is extended per code by the task that owns the job (procurement_job.py, not a file of this task)
    """The nightly scorecard snapshot runs as a job of the app, 200 suppliers per committed batch.

    Requirements: FR-SPM-0083, NFR-SPM-0006
    """
    _inherit = 'procurement.job'

    code = fields.Selection(selection_add=[('scorecard', 'Supplier scorecards')],
                            ondelete={'scorecard': 'cascade'})

    def _spm_collect_scorecard(self):
        """Return the suppliers a scorecard template applies to and those with an ended period still open.

        Requirements: FR-SPM-0083
        """
        ended = self.env['procurement.supplier.scorecard'].search([
            ('period_closed', '=', False), ('period_end', '<', fields.Date.context_today(self))]).partner_id
        return (performance.suppliers_with_template(self.env) | ended).sorted('id')

    def _spm_process_scorecard(self, partner):
        """Finish the ended periods and snapshot the scorecards of one supplier for today; return 0 (no notification is sent).

        Requirements: FR-SPM-0083
        """
        today = fields.Date.context_today(self)
        cards = self.env['procurement.supplier.scorecard']
        cards._spm_close_ended(partner, today)
        for template in performance.templates_for(self.env, partner):
            cards._spm_snapshot(template, partner, today)
        return 0
