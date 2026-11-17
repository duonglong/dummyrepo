from odoo import api, fields, models
from odoo.exceptions import UserError

from ..const import BULK_SKIPPED_LISTED, ISSUE_OPEN_STATES, ISSUE_STATE_MOVES
from .issue import OPERATOR_GROUP

# The status each bulk action moves the issues to; the other actions change a work field only.
BULK_TARGET_STATE = {'resolve': 'resolved', 'ignore': 'ignored', 'reopen': 'new'}
BULK_WORK_FIELDS = {'assign': ('user_id',), 'priority': ('priority',), 'deadline': ('deadline',)}


class DataQualityIssueBulk(models.TransientModel):
    _name = 'data.quality.issue.bulk'
    _description = 'Bulk Issue Action'

    issue_ids = fields.Many2many('data.quality.issue', string='Issues')
    action = fields.Selection([
        ('assign', 'Assign'),
        ('priority', 'Set priority'),
        ('deadline', 'Set deadline'),
        ('resolve', 'Resolve'),
        ('ignore', 'Ignore'),
        ('reopen', 'Reopen'),
    ], required=True, default='assign')
    user_id = fields.Many2one(
        'res.users', string='Assign To', domain=lambda self: [('all_group_ids', 'in', self.env.ref(OPERATOR_GROUP).ids)])
    priority = fields.Selection([
        ('low', 'Low'),
        ('normal', 'Normal'),
        ('high', 'High'),
        ('urgent', 'Urgent'),
    ], default='normal')
    deadline = fields.Date()
    reason = fields.Text(help="Why the issues are ignored; kept on each of them.")
    result_message = fields.Text(readonly=True)

    def _bulk_values(self):
        """The values written on every issue the action can change."""
        self.ensure_one()
        if self.action in BULK_TARGET_STATE:
            values = {'state': BULK_TARGET_STATE[self.action]}
            if self.action == 'ignore':
                if not (self.reason or '').strip():
                    raise UserError(self.env._("A reason is needed to ignore issues: say why."))
                values['ignore_reason'] = self.reason
            return values
        if self.action == 'assign' and not self.user_id:
            raise UserError(self.env._("Choose the user to assign the issues to."))
        if self.action == 'deadline' and not self.deadline:
            raise UserError(self.env._("Choose the deadline to set."))
        return {
            name: self._fields[name].convert_to_write(self[name], self)
            for name in BULK_WORK_FIELDS[self.action]}

    def _can_change(self, issue):
        """Whether the status of the issue allows this action, and, to assign, its company is visible to the assignee.

        Requirements: FR-DGQ-0027
        """
        if self.action == 'reopen':
            return issue.state in ('resolved', 'ignored')
        if self.action in BULK_TARGET_STATE:
            return BULK_TARGET_STATE[self.action] in ISSUE_STATE_MOVES[issue.state]
        if issue.state not in ISSUE_OPEN_STATES:
            return False
        return self.action != 'assign' or self._assignee_sees(issue)

    def _assignee_sees(self, issue):
        return not issue.company_id or issue.company_id <= self.user_id.company_ids

    def _skip_reason(self, issue):
        """Why the issue is left as it is."""
        state = dict(issue._fields['state']._description_selection(self.env))[issue.state]
        if issue.state in ISSUE_OPEN_STATES and self.action == 'assign':
            return self.env._(
                "%(name)s is in the company %(company)s, which %(user)s cannot see",
                name=issue.display_name, company=issue.company_id.name, user=self.user_id.name)
        if self.action in BULK_TARGET_STATE and BULK_TARGET_STATE[self.action] == issue.state:
            return self.env._("%(name)s is already %(state)s", name=issue.display_name, state=state)
        if issue.state in ('resolved', 'ignored'):
            return self.env._("%(name)s is %(state)s: reopen it first", name=issue.display_name, state=state)
        return self.env._("%(name)s is %(state)s: nothing to change", name=issue.display_name, state=state)

    def _result_message(self, changed, skipped):
        lines = [self.env._("%s changed.", self.env._("1 issue") if len(changed) == 1 else self.env._("%s issues", len(changed)))]
        if skipped:
            lines.append(self.env._("%s skipped:", self.env._("1 issue") if len(skipped) == 1 else self.env._("%s issues", len(skipped))))
            lines += ["- %s" % self._skip_reason(issue) for issue in skipped[:BULK_SKIPPED_LISTED]]
            if len(skipped) > BULK_SKIPPED_LISTED:
                lines.append(self.env._("and %s more", len(skipped) - BULK_SKIPPED_LISTED))
        return "\n".join(lines)

    def action_apply(self):
        """Apply the action to every selected issue whose status allows it, then report.

        One write on all the issues that can change, so that each rule of the issue (status
        table, assignee check, history, one notice per person) holds as for a single issue;
        those that cannot change are listed with the reason, not an error.

        Requirements: FR-DGQ-0027, FR-DGQ-0029
        """
        self.ensure_one()
        values = self._bulk_values()
        changed = self.issue_ids.filtered(self._can_change)
        skipped = self.issue_ids - changed
        changed.write(values)
        self.result_message = self._result_message(changed, skipped)
        return {
            'type': 'ir.actions.act_window', 'res_model': self._name, 'res_id': self.id,
            'view_mode': 'form', 'views': [(False, 'form')], 'target': 'new',
        }
