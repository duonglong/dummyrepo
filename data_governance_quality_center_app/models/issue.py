from odoo import api, fields, models
from odoo.exceptions import UserError

from ..const import ISSUE_PRIORITY_BY_SEVERITY, ISSUE_STATE_MOVES
from ..services import explain


class DataQualityIssue(models.Model):
    """The work item for one record failing one rule: one per pair, reopened rather than doubled.

    Requirements: BR-DGQ-0003, BR-DGQ-0004, FR-DGQ-0021, FR-DGQ-0022, FR-DGQ-0023
    """
    _name = 'data.quality.issue'
    _inherit = ['data.quality.result.mixin', 'mail.thread', 'mail.activity.mixin']
    _description = 'Data Quality Issue'
    _order = 'id desc'

    name = fields.Char(compute='_compute_name', store=True)
    rule_id = fields.Many2one('data.quality.rule', required=True, ondelete='restrict', index=True)
    rule_version = fields.Integer()
    res_id = fields.Many2oneReference(string='Record', model_field='res_model', required=True, index=True)
    record_name = fields.Char()
    severity = fields.Selection([
        ('info', 'Information'),
        ('warning', 'Warning'),
        ('critical', 'Critical'),
    ])
    explanation_code = fields.Char()
    explanation_params = fields.Json()
    explanation = fields.Text(compute='_compute_explanation')
    scan_id = fields.Many2one('data.quality.scan', string='Last Scan', ondelete='set null')
    first_scan_id = fields.Many2one('data.quality.scan', string='First Scan', ondelete='set null')
    first_found = fields.Datetime(default=fields.Datetime.now)
    last_found = fields.Datetime(default=fields.Datetime.now)
    is_illustrative = fields.Boolean()

    state = fields.Selection([
        ('new', 'New'),
        ('assigned', 'Assigned'),
        ('in_progress', 'In Progress'),
        ('resolved', 'Resolved'),
        ('ignored', 'Ignored'),
    ], default='new', required=True, index=True, tracking=True, copy=False)
    priority = fields.Selection([
        ('low', 'Low'),
        ('normal', 'Normal'),
        ('high', 'High'),
        ('urgent', 'Urgent'),
    ], tracking=True)
    user_id = fields.Many2one('res.users', string='Assigned To', ondelete='set null', tracking=True)
    deadline = fields.Date(tracking=True)
    ignore_reason = fields.Text()

    date_resolved = fields.Datetime(readonly=True, copy=False)
    resolution_hours = fields.Float(readonly=True, copy=False)
    resolved_by_scan = fields.Boolean(readonly=True, copy=False)

    _record_rule_unique = models.Constraint(
        'UNIQUE (rule_id, res_model, res_id)', 'A record has one issue for each rule.')
    _state_company_idx = models.Index('(state, company_id)')

    @api.depends('rule_id.name', 'record_name', 'res_id')
    def _compute_name(self):
        for issue in self:
            issue.name = "%s: %s" % (issue.rule_id.name, issue.record_name or issue.res_id)

    @api.depends('explanation_code', 'explanation_params', 'res_model', 'rule_id.advice')
    @api.depends_context('lang')
    def _compute_explanation(self):
        """Render the stored code in the reader's language, the advice of the rule below it.

        Requirements: FR-DGQ-0021
        """
        for issue in self:
            issue.explanation = explain.render(
                self.env, issue.explanation_code, issue.explanation_params, issue.rule_id.advice) or False

    @api.model_create_multi
    def create(self, vals_list):
        """Start the priority from the severity unless it is given.

        Requirements: FR-DGQ-0021
        """
        for vals in vals_list:
            if not vals.get('priority') and vals.get('severity'):
                vals['priority'] = ISSUE_PRIORITY_BY_SEVERITY[vals['severity']]
        return super().create(vals_list)

    def write(self, vals):
        """Check each status change against the table, whoever asks (button, list, RPC, scan).

        Setting the person on a New issue makes it Assigned; removing the person from an
        Assigned issue makes it New. Ignoring needs a reason; resolving and reopening keep
        the resolution date and hours.

        Requirements: BR-DGQ-0004
        """
        if not {'state', 'user_id'} & set(vals):
            return super().write(vals)
        for issue in self:
            super(DataQualityIssue, issue).write(issue._dgq_state_values(vals))
        return True

    def _dgq_state_values(self, vals):
        """The values to write on this issue, the status change checked and its side effects added."""
        self.ensure_one()
        vals = dict(vals)
        target = vals.get('state')
        if target is None:
            if 'user_id' in vals:
                if self.state == 'new' and vals['user_id']:
                    vals['state'] = 'assigned'
                elif self.state == 'assigned' and not vals['user_id']:
                    vals['state'] = 'new'
            return vals
        if target == self.state:
            del vals['state']
            return vals
        if target not in ISSUE_STATE_MOVES[self.state]:
            labels = dict(self._fields['state']._description_selection(self.env))
            raise UserError(self.env._(
                "An issue that is %(state)s can only become: %(allowed)s.",
                state=labels[self.state], allowed=", ".join(labels[name] for name in ISSUE_STATE_MOVES[self.state])))
        if target == 'ignored' and not (vals.get('ignore_reason') or self.ignore_reason):
            raise UserError(self.env._("Say why the issue is ignored."))
        if target == 'resolved':
            now = fields.Datetime.now()
            vals.update(
                date_resolved=now,
                resolution_hours=max((now - (self.first_found or now)).total_seconds() / 3600.0, 0.0))
        elif self.state == 'resolved':
            vals.update(date_resolved=False, resolution_hours=0.0, resolved_by_scan=False)
        return vals
