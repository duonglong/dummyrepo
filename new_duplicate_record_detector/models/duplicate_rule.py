import logging

from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import AccessError, ValidationError
from odoo.modules import module as odoo_module

_logger = logging.getLogger(__name__)

WATCHED_MODELS = ('res.partner', 'product.template')
STALE_CHECK_HOURS = 1


class DuplicateRule(models.Model):
    _name = 'duplicate.rule'
    _description = 'Duplicate Detection Rule'

    name = fields.Char(required=True)
    model_id = fields.Many2one(
        'ir.model', string='Applies to', required=True, ondelete='cascade',
        domain=[('model', 'in', WATCHED_MODELS)],
        help="Contacts or product templates: the records this rule looks at.")
    model_name = fields.Char(related='model_id.model', store=True, index=True)
    active = fields.Boolean(
        default=True, help="Switch the rule off to keep it without using it.")
    condition_ids = fields.One2many(
        'duplicate.rule.condition', 'rule_id', string='Conditions', copy=True,
        help="Every condition must match for two records to be possible duplicates.")

    group_ids = fields.One2many(
        'duplicate.group', 'rule_id', string='Sets found', copy=False)
    open_group_count = fields.Integer(
        compute='_compute_open_group_count', help="Sets of possible duplicates of this rule still to review.")

    check_state = fields.Selection(
        [('idle', 'Idle'), ('queued', 'Queued'), ('running', 'Running'),
         ('done', 'Done'), ('failed', 'Failed')],
        string='Full check', required=True, default='idle', copy=False)
    check_user_id = fields.Many2one(
        'res.users', string='Asked by', copy=False, ondelete='set null',
        help="Who asked for the last full check: the result is notified to this person.")
    last_check_date = fields.Datetime(copy=False)
    last_check_group_count = fields.Integer(
        string='Sets found', copy=False, help="Sets found by the last full check.")
    last_check_error = fields.Char(copy=False)

    matchable_field_ids = fields.Many2many(
        'ir.model.fields', compute='_compute_matchable_field_ids',
        help="Fields of the record type that a condition may use.")

    @api.depends('model_id')
    def _compute_matchable_field_ids(self) -> None:
        """Requirements: FR-DRD-0002 (offer only fields a condition may use)."""
        candidates = self.env['ir.model.fields'].search(
            [('model_id', 'in', self.model_id.ids)]).filtered(
                self.env['duplicate.rule.condition']._is_matchable)
        for rule in self:
            rule.matchable_field_ids = candidates.filtered(
                lambda f: f.model_id == rule.model_id)

    @api.depends('group_ids.state')
    def _compute_open_group_count(self) -> None:
        """Requirements: FR-DRD-0004 (count the open sets of each rule)."""
        counts = dict(self.env['duplicate.group']._read_group(
            [('rule_id', 'in', self.ids), ('state', '=', 'open')], ['rule_id'], ['__count']))
        for rule in self:
            rule.open_group_count = counts.get(rule, 0)

    @api.model_create_multi
    def create(self, vals_list: list[dict]):
        """Requirements: FR-DRD-0002 (a rule created without conditions is refused too).

        The constraint on condition_ids only runs when the key is sent, so an import or an
        API call that leaves it out would store a rule without condition.
        """
        rules = super().create(vals_list)
        rules._check_has_condition()
        return rules

    def write(self, vals: dict):
        """Requirements: FR-DRD-0002 (check the conditions once, on the final state of a save).

        The one2many write deletes conditions before it creates the new ones; the context key
        tells the condition's unlink not to judge that intermediate state.
        """
        return super(DuplicateRule, self.with_context(drd_rule_write=True)).write(vals)

    @api.constrains('model_id')
    def _check_model(self) -> None:
        """Requirements: FR-DRD-0001 (only contacts or product templates are watched)."""
        for rule in self.filtered(lambda r: r.model_id.model not in WATCHED_MODELS):
            raise ValidationError(self.env._(
                "A rule can only look at contacts or product templates, not at %s.",
                rule.model_id.name))

    @api.constrains('condition_ids')
    def _check_has_condition(self) -> None:
        """Requirements: FR-DRD-0002 (a rule needs at least one condition)."""
        for rule in self.filtered(lambda r: not r.condition_ids):
            raise ValidationError(self.env._(
                "The rule \"%s\" needs at least one condition: without one it would flag "
                "every record as a duplicate of every other.", rule.name))

    @api.constrains('model_id')
    def _check_conditions_fit_model(self) -> None:
        """Requirements: FR-DRD-0002 (conditions keep to the fields of the rule's record type)."""
        self.condition_ids._check_field_is_matchable()

    def action_full_check(self) -> bool:
        """Requirements: FR-DRD-0005, FR-DRD-0018.

        Queue a full check of these rules for the current user and wake the background job that
        runs it. Only duplicate managers may ask for it.
        """
        if not self.env.user.has_group('new_duplicate_record_detector.group_duplicate_manager'):
            raise AccessError(self.env._("Only duplicate managers can run a full check."))
        self.write({'check_state': 'queued', 'check_user_id': self.env.user.id,
                    'last_check_error': False})
        # sudo: a manager does not own the cron job that is woken
        self.env.ref('new_duplicate_record_detector.ir_cron_full_check').sudo()._trigger()
        return True

    @api.model
    def _cron_run_full_checks(self) -> None:
        """Requirements: FR-DRD-0005, NFR-DRD-0002.

        Fail the rules a dead worker left running, then run the queued rules one by one, each
        committed as running before it starts and again with its result, so a job killed by its
        time limit loses only the rule it was on.
        """
        jobs = self
        if odoo_module.current_test:
            # The job runs on its own connection, which cannot see what a running test has not
            # committed: in a test it works on the test's own transaction instead.
            jobs = odoo_module.current_test.env['duplicate.rule']
        jobs._fail_stale_checks()
        while rule := jobs._claim_queued_check():
            rule._run_full_check()
            jobs._commit_check_progress()

    @api.model
    def _fail_stale_checks(self) -> None:
        """Requirements: NFR-DRD-0002 (a rule running longer than STALE_CHECK_HOURS is failed)."""
        limit = fields.Datetime.now() - timedelta(hours=STALE_CHECK_HOURS)
        stale = self.with_context(active_test=False).search(
            [('check_state', '=', 'running'), ('write_date', '<', limit)])
        for rule in stale:
            rule._fail_check(self.env._("The check did not finish within %s hour(s).", STALE_CHECK_HOURS))

    @api.model
    def _claim_queued_check(self) -> 'DuplicateRule':
        """Requirements: FR-DRD-0005. Take the next queued rule no other worker holds and mark it running."""
        self.env.flush_all()
        self.env.cr.execute(
            "SELECT id FROM duplicate_rule WHERE check_state = 'queued' "
            "ORDER BY id LIMIT 1 FOR UPDATE SKIP LOCKED")
        rule = self.browse([row[0] for row in self.env.cr.fetchall()])
        if rule:
            rule.check_state = 'running'
            self._commit_check_progress()
        return rule

    @api.model
    def _commit_check_progress(self) -> None:
        """Commit so the state of a rule survives a job killed by its time limit (a test keeps its transaction)."""
        if not odoo_module.current_test:
            self.env.cr.commit()

    def _run_full_check(self) -> None:
        """Requirements: FR-DRD-0005, FR-DRD-0006, NFR-DRD-0002.

        Replace the open sets of the rule with the ones just found, in one transaction, and record
        the result or the error on the rule.
        """
        self.ensure_one()
        self.check_state = 'running'
        try:
            with self.env.cr.savepoint():
                count = self.env['duplicate.full.check'].replace_open_sets(self)
        except Exception as error:  # noqa: BLE001 - any failure closes the check as failed
            _logger.exception("Full check of rule %s failed", self.id)
            self._fail_check(str(error) or type(error).__name__)
            return
        self.write({'check_state': 'done', 'last_check_date': fields.Datetime.now(),
                    'last_check_group_count': count, 'last_check_error': False})
        self._notify_check_result(count)

    def _fail_check(self, reason: str) -> None:
        """Requirements: FR-DRD-0005. Close the check as failed and tell the person who asked."""
        self.write({'check_state': 'failed', 'last_check_error': reason})
        self.check_user_id._bus_send('simple_notification', {
            'title': self.env._("Full check failed"),
            'message': self.env._("The rule %(rule)s failed: %(reason)s", rule=self.name, reason=reason),
            'type': 'danger',
        })

    def _notify_check_result(self, count: int) -> None:
        """Tell the person who asked for the check how many sets it found."""
        self.check_user_id._bus_send('simple_notification', {
            'title': self.env._("Full check finished"),
            'message': self.env._("%(count)s sets found by the rule %(rule)s", count=count, rule=self.name),
            'type': 'success',
        })
