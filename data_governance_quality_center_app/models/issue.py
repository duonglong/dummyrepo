from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

from ..const import ISSUE_PRIORITY_BY_SEVERITY, ISSUE_STATE_MOVES, NOTIFY_EMAIL_DEFAULT, NOTIFY_EMAIL_PARAMETER
from ..services import audit, explain

OPERATOR_GROUP = 'data_governance_quality_center_app.group_dgq_operator'
ASSIGNED_TEMPLATE = 'data_governance_quality_center_app.mail_template_issue_assigned'
ESCALATED_TEMPLATE = 'data_governance_quality_center_app.mail_template_issue_escalated'


class DataQualityIssue(models.Model):
    """The work item for one record failing one rule: one per pair, reopened rather than doubled.

    Requirements: BR-DGQ-0003, BR-DGQ-0004, FR-DGQ-0021, FR-DGQ-0022, FR-DGQ-0023, FR-DGQ-0024,
    FR-DGQ-0026, FR-DGQ-0028, FR-DGQ-0030, FR-DGQ-0067
    """
    _name = 'data.quality.issue'
    _inherit = ['data.quality.result.mixin', 'mail.thread', 'mail.activity.mixin']
    _description = 'Data Quality Issue'
    _order = 'id desc'
    # whoever may see an issue may comment on it, as on partners and users (FR-DGQ-0026)
    _mail_post_access = 'read'

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
    ], default='new', required=True, index=True, tracking=True, copy=False, group_expand=True)
    priority = fields.Selection([
        ('low', 'Low'),
        ('normal', 'Normal'),
        ('high', 'High'),
        ('urgent', 'Urgent'),
    ], tracking=True)
    user_id = fields.Many2one('res.users', string='Assigned To', ondelete='set null', tracking=True)
    team_id = fields.Many2one('data.quality.team', string='Team', ondelete='set null', index='btree_not_null')
    deadline = fields.Date(tracking=True)
    escalated_deadline = fields.Date(
        readonly=True, copy=False, help="The deadline for which the issue was escalated.")
    ignore_reason = fields.Text()

    date_resolved = fields.Datetime(readonly=True, copy=False)
    resolution_hours = fields.Float(readonly=True, copy=False, aggregator='avg')
    resolved_by_scan = fields.Boolean(readonly=True, copy=False)

    _dgq_work_fields = frozenset({'state', 'user_id', 'priority', 'deadline', 'ignore_reason'})

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
        the resolution date and hours. The assignee must be an operator of the issue's company;
        the history line is made when the change is written, so that each person's change keeps
        its own author and time even inside one transaction (what is still pending from earlier
        in the transaction is settled first); the new assignee is notified once for all the
        issues of this write.

        A user writes only the work fields: the finding comes from the scan.

        Every assignment, resolution, ignoring and reopening is noted in the audit trail.

        Requirements: BR-DGQ-0004, FR-DGQ-0024, FR-DGQ-0025, FR-DGQ-0026, FR-DGQ-0028, FR-DGQ-0070, FR-DGQ-0072
        """
        if not self.env.su:
            forbidden = set(vals) - self._dgq_work_fields
            if forbidden:
                labels = sorted(
                    self._fields[name]._description_string(self.env) if name in self._fields else name
                    for name in forbidden)
                raise UserError(self.env._(
                    "These issue fields come from the scan, not from hand: %s.", ", ".join(labels)))
        if not {'state', 'user_id'} & set(vals):
            return super().write(vals)
        self._track_finalize()
        assignee = self.env['res.users'].browse(vals.get('user_id'))
        if assignee:
            self._dgq_check_assignee(assignee)
        newly_assigned = self.filtered(lambda issue: issue.user_id != assignee) if assignee else self.browse()
        before = {issue.id: (issue.state, issue.user_id) for issue in self}
        for issue in self:
            super(DataQualityIssue, issue).write(issue._dgq_state_values(vals))
        self._track_finalize()
        self._dgq_audit_changes(before)
        if assignee != self.env.user and not self.env.context.get('dgq_no_assign_notice'):
            newly_assigned._dgq_notify_assignee(assignee)
        return True

    def _dgq_audit_changes(self, before):
        """Write the audit entries of the issues whose person or status changed since ``before``.

        ``before`` maps an issue id to its (status, person) before the write.

        Requirements: FR-DGQ-0072
        """
        entries = []
        for issue in self:
            state, user = before[issue.id]
            if issue.user_id and issue.user_id != user:
                entries.append(audit.entry_values(
                    self.env, 'issue_assigned', issue, changes={'user_id': [user.name or False, issue.user_id.name]}))
            if issue.state == state:
                continue
            event = {'resolved': 'issue_resolved', 'ignored': 'issue_ignored'}.get(issue.state)
            if issue.state == 'new' and state in ('resolved', 'ignored'):
                event = 'issue_reopened'
            if event:
                entries.append(audit.entry_values(
                    self.env, event, issue, changes={'state': [state, issue.state]}))
        audit.log_many(self.env, entries)

    def _dgq_state_values(self, vals):
        """The values to write on this issue, the status change checked and its side effects added.

        Requirements: FR-DGQ-0025
        """
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
            allowed = ", ".join(labels[name] for name in ISSUE_STATE_MOVES[self.state])
            if self.state in ('resolved', 'ignored'):
                raise ValidationError(self.env._(
                    "This issue is %(state)s: reopen it (New) first; it can only become: %(allowed)s.",
                    state=labels[self.state], allowed=allowed))
            raise ValidationError(self.env._(
                "An issue that is %(state)s can only become: %(allowed)s.", state=labels[self.state], allowed=allowed))
        if target == 'ignored' and not (vals.get('ignore_reason') or self.ignore_reason):
            raise ValidationError(self.env._("A reason is needed to ignore an issue: say why."))
        if target == 'resolved':
            now = fields.Datetime.now()
            vals.update(
                date_resolved=now,
                resolution_hours=max((now - (self.first_found or now)).total_seconds() / 3600.0, 0.0))
        elif self.state == 'resolved':
            vals.update(date_resolved=False, resolution_hours=0.0, resolved_by_scan=False)
        return vals

    def _dgq_check_assignee(self, assignee):
        """Only users of the issue's company with at least operator access can be assigned.

        Requirements: FR-DGQ-0024
        """
        if not assignee.has_group(OPERATOR_GROUP):
            raise UserError(self.env._("%s has no operator access to data quality, so cannot be assigned.", assignee.name))
        outside = self.company_id - assignee.company_ids
        if outside:
            raise UserError(self.env._(
                "%(user)s cannot see the company %(company)s of the issue.",
                user=assignee.name, company=", ".join(outside.mapped('name'))))

    def _dgq_notify_assignee(self, assignee):
        """One notice for all the issues: in Odoo always, by e-mail when the policy says so.

        Requirements: FR-DGQ-0028
        """
        if not self:
            return
        # the notice is written in the language of the person it is for
        issues = self.with_context(lang=assignee.lang)
        template = self.env.ref(ASSIGNED_TEMPLATE).with_context(lang=assignee.lang, dgq_issues=issues)
        first = issues[0]
        subject = template._render_field('subject', first.ids)[first.id]
        body = template._render_field('body_html', first.ids)[first.id]
        first.message_notify(partner_ids=assignee.partner_id.ids, subject=subject, body=body)
        # sudo: the policy is read for every user, not only administrators
        sends_email = self.env['ir.config_parameter'].sudo().get_bool(NOTIFY_EMAIL_PARAMETER, NOTIFY_EMAIL_DEFAULT)
        if sends_email and assignee.notification_type != 'email' and assignee.email:
            # sudo: an operator may not create outgoing mail; the notice is the app's own
            self.env['mail.mail'].sudo().create({
                'subject': subject, 'body_html': body, 'email_to': assignee.email, 'auto_delete': True})

    def action_open_record(self):
        """Open the record behind the issue under the user's own rights, or say why it cannot be.

        Requirements: FR-DGQ-0030
        """
        return self._dgq_open_record()

    def _dgq_escalate(self, users):
        """Raise the priority of each issue by one level, note it, and tell each user once about all of them.

        The deadline escalated for is kept on the issue, so the same deadline is never escalated twice.
        Issues of the same priority and deadline are written together.

        Requirements: FR-DGQ-0067
        """
        levels = [level for level, _label in self._fields['priority'].selection]
        for (priority, deadline), issues in self.grouped(lambda issue: (issue.priority, issue.deadline)).items():
            position = levels.index(priority) if priority else 0
            issues.write({'priority': levels[min(position + 1, len(levels) - 1)], 'escalated_deadline': deadline})
        for issue in self:
            issue.message_post(
                body=self.env._("Escalated: the deadline %s has passed.", issue.deadline),
                subtype_xmlid='mail.mt_note')
        for user in users:
            issues = self.with_context(lang=user.lang)
            template = self.env.ref(ESCALATED_TEMPLATE).with_context(lang=user.lang, dgq_issues=issues)
            subject = template._render_field('subject', issues[:1].ids)[issues[:1].id]
            body = template._render_field('body_html', issues[:1].ids)[issues[:1].id]
            self.env['mail.thread'].message_notify(partner_ids=user.partner_id.ids, subject=subject, body=body)
