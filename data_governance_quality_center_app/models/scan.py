import logging
import time

from odoo import api, fields, models
from odoo.exceptions import UserError
from odoo.fields import Domain
from odoo.tools import SQL

from ..const import JOB_BATCH_SIZE
from ..services import scan as scan_service
from ..services import validation

_logger = logging.getLogger(__name__)


class DataQualityScan(models.Model):
    """One run of rules over records: scope, state, progress and counts.

    Requirements: FR-DGQ-0015, FR-DGQ-0018, FR-DGQ-0020, FR-DGQ-0019
    """
    _name = 'data.quality.scan'
    _inherit = ['data.quality.job.mixin']
    _description = 'Data Quality Scan'
    _order = 'id desc'
    _dgq_cron_xmlid = 'data_governance_quality_center_app.ir_cron_dgq_scan'
    #: the only fields a user may set, and only on a draft scan; the rest moves through the actions
    _dgq_scope_fields = frozenset({'rule_ids', 'model_ids', 'company_ids'})

    name = fields.Char(string='Reference', readonly=True, copy=False, default='/')
    trigger = fields.Selection([
        ('manual', 'Manual'),
        ('automation', 'Automation'),
        ('event', 'Event'),
    ], string='Started By', default='manual', required=True, copy=False)
    rule_ids = fields.Many2many(
        'data.quality.rule', string='Rules', copy=False,
        help="The rules to scan. Leave empty to scan every active rule of the supported models.")
    model_ids = fields.Many2many(
        'ir.model', string='Models', copy=False,
        help="Only the rules of these models are scanned. Leave empty for every supported model.")
    company_ids = fields.Many2many(
        'res.company', string='Companies', copy=False,
        help="The companies whose records are scanned. Leave empty for all your allowed companies.")
    rules_not_run = fields.Json(string='Rules Not Run', copy=False, readonly=True)
    rules_not_run_text = fields.Text(compute='_compute_rules_not_run_text')

    records_scanned = fields.Integer(copy=False, readonly=True)
    checks_passed = fields.Integer(copy=False, readonly=True)
    checks_failed = fields.Integer(copy=False, readonly=True)
    checks_not_applicable = fields.Integer(copy=False, readonly=True)
    records_error = fields.Integer(string='Records With Errors', copy=False, readonly=True)
    records_warning = fields.Integer(string='Records With Warnings', copy=False, readonly=True)
    issues_created = fields.Integer(copy=False, readonly=True)
    issues_updated = fields.Integer(copy=False, readonly=True)
    issues_resolved = fields.Integer(copy=False, readonly=True)

    # the job progress is written by the app only: shown read-only on screen
    state = fields.Selection(readonly=True)
    user_id = fields.Many2one(readonly=True)
    date_queued = fields.Datetime(readonly=True)
    date_start = fields.Datetime(readonly=True)
    date_end = fields.Datetime(readonly=True)
    heartbeat = fields.Datetime(readonly=True)
    total_count = fields.Integer(readonly=True)
    done_count = fields.Integer(readonly=True)
    error_message = fields.Text(readonly=True)

    line_ids = fields.One2many('data.quality.scan.line', 'scan_id', string='Results')
    checkpoint_ids = fields.One2many('data.quality.scan.checkpoint', 'scan_id', string='Checkpoints')
    tally_ids = fields.One2many('data.quality.scan.tally', 'scan_id', string='Tallies')

    @api.model
    def _access_domain(self, operation):
        """Limit scans to the allowed companies; a scan without company is open to all.

        The requester keeps access to his own scan, since an operator may scan a company
        outside his own (accepted); sudo and the superuser are not filtered.

        Requirements: FR-DGQ-0071, BR-DGQ-0011
        """
        domain = super()._access_domain(operation)
        if operation == 'create':
            return domain
        return domain & (
            Domain('company_ids', '=', False)
            | Domain('company_ids', 'in', self.env.companies.ids)
            | Domain('create_uid', '=', self.env.uid))

    @api.model_create_multi
    def create(self, vals_list):
        """Give each scan its reference and link its companies as the app.

        A user sets only the scope of a draft scan; state, counts and requester are written
        by the app (defaults passed in the context included). An operator may name a company
        of the scan that his own companies do not list, which Odoo would refuse as a read of
        that company; the scan records the choice, the access to the results stays limited by
        company (BR-DGQ-0011).

        Requirements: FR-DGQ-0015, FR-DGQ-0018, FR-DGQ-0070
        """
        scans = self
        if not self.env.su:
            for vals in vals_list:
                self._dgq_check_user_values({key for key, value in vals.items()
                                             if not (key == 'state' and value == 'draft')})
            # context defaults would set the fields the user may not
            scans = self.with_context({
                key: value for key, value in self.env.context.items()
                if not key.startswith('default_') or key[len('default_'):] in self._dgq_scope_fields})
        # sudo: an operator may create a scan without reading the sequence
        sequence = self.env['ir.sequence'].sudo()
        vals_list = [dict(vals) for vals in vals_list]
        companies = []
        for vals in vals_list:
            companies.append(vals.pop('company_ids', None))
            if vals.get('name', '/') == '/':
                vals['name'] = sequence.next_by_code('data.quality.scan') or '/'
        scans = super(DataQualityScan, scans).create(vals_list)
        for scan, commands in zip(scans, companies):
            if commands:
                scan.sudo().write({'company_ids': commands})  # sudo: companies the operator may not read
        return scans

    def write(self, vals):
        """A user changes only the scope, and only while every scan is draft.

        A queued or running scan keeps the scope its overlap and not-run checks were made on.

        Requirements: FR-DGQ-0018, FR-DGQ-0020, FR-DGQ-0070
        """
        if not self.env.su:
            self._dgq_check_user_values(set(vals))
            if any(scan.state != 'draft' for scan in self):
                raise UserError(self.env._("The scope of a scan can only be changed while it is a draft."))
        return super().write(vals)

    @api.model
    def _dgq_check_user_values(self, names):
        """Refuse the fields a user may not set on a scan."""
        forbidden = names - self._dgq_scope_fields
        if forbidden:
            labels = sorted(
                self._fields[name]._description_string(self.env) if name in self._fields else name
                for name in forbidden)
            raise UserError(self.env._(
                "These scan fields are set by the app, not by hand: %s. "
                "A scan moves on only through Start, Resume and Cancel.",
                ", ".join(labels)))

    def action_cancel(self):
        """Cancel the scans the user may change; the app writes the new state.

        Requirements: FR-DGQ-0015
        """
        self.check_access('write')
        return super(DataQualityScan, self.sudo()).action_cancel()  # sudo: after the access check

    @api.depends('rules_not_run')
    def _compute_rules_not_run_text(self):
        # sudo: the rule names are shown to whoever can read the scan
        rules = self.env['data.quality.rule'].sudo().with_context(active_test=False)
        for scan in self:
            scan.rules_not_run_text = "\n".join(
                "%s: %s" % (rules.browse(int(rule_id)).display_name, reason)
                for rule_id, reason in (scan.rules_not_run or {}).items())

    # ---- start: scope and overlap -------------------------------------------

    def _dgq_enqueue(self):
        """Resolve the scope, refuse an overlap, then queue (also on resume).

        Every scan of the call is checked against the queued and running scans and against
        the scans before it in the call, before any is queued.

        Requirements: FR-DGQ-0015, FR-DGQ-0018
        """
        self.check_access('write')
        for scan in self:
            scan._dgq_resolve_scope()
        self._dgq_lock_scope_rules()
        for index, scan in enumerate(self):
            scan._dgq_refuse_overlap(self[:index])
        return super(DataQualityScan, self.sudo())._dgq_enqueue()  # sudo: the app writes the state after the checks

    def _dgq_lock_scope_rules(self):
        """Touch the scope's rule rows so two starts of the same rule cannot both pass.

        A request runs on a snapshot taken before this call, so a plain lock would still let
        the second start miss the first one's queued scan. Updating the rows both starts share
        makes the second one fail with a serialization error once the first commits; Odoo then
        repeats the request on a fresh snapshot, which sees the queued scan.

        Requirements: FR-DGQ-0018
        """
        rule_ids = self.sudo().rule_ids.ids  # sudo: the requester may not read every rule
        self.env.cr.execute(SQL(
            "UPDATE data_quality_rule SET write_date = write_date WHERE id IN %s", tuple(rule_ids)))

    def _dgq_resolve_scope(self):
        """Store the real rules, models and companies: an empty choice means everything allowed.

        Requirements: FR-DGQ-0015
        """
        self.ensure_one()
        scan = self.sudo()  # sudo: the scope is read as stored, whatever the requester may read
        Rule = self.env['data.quality.rule'].sudo()  # sudo: an empty scope covers every active rule
        companies = scan.company_ids or self.env.user.sudo().company_ids  # sudo: all his allowed companies
        rules = scan.rule_ids
        if rules:
            rules = rules.filtered('active')
        else:
            rules = Rule.search([('active', '=', True)]).filtered(
                lambda rule: self.env['data.quality.model.config']._get_status(rule.model_name)
                in ('supported', 'restricted'))
        if scan.model_ids:
            rules = rules.filtered(lambda rule: rule.model_id in scan.model_ids)
        rules = rules.filtered(lambda rule: not rule.company_ids or rule.company_ids & companies)
        if not rules:
            raise UserError(self.env._("There is no active rule to scan in this scope."))
        scan.write({  # sudo: the app writes the resolved scope
            'rule_ids': [fields.Command.set(rules.ids)],
            'model_ids': [fields.Command.set(rules.model_id.ids)],
            'company_ids': [fields.Command.set(companies.ids)],
        })

    def _dgq_refuse_overlap(self, starting=None):
        """Refuse a scan that shares a rule and a company with a queued or running scan,
        or with one of the ``starting`` scans queued in the same call.

        Requirements: FR-DGQ-0018
        """
        self.ensure_one()
        # sudo: the scope is read as stored, whatever companies or rules the requester may read now,
        # and the running scan of another user still blocks this one
        Scan = self.sudo()
        scope = [('rule_ids', 'in', Scan.rule_ids.ids), ('company_ids', 'in', Scan.company_ids.ids)]
        busy = Scan.search([('id', '!=', self.id), ('state', 'in', ('queued', 'running'))] + scope, limit=1)
        sibling = Scan.browse()
        if not busy and starting:
            sibling = Scan.search([('id', 'in', starting.ids)] + scope, limit=1)
        other = busy or sibling
        if not other:
            return
        # sudo: the shared rule and company may not be readable by the requester
        shared_rule = (other.rule_ids & Scan.rule_ids)[:1].display_name
        shared_company = (other.company_ids & Scan.company_ids)[:1].display_name
        if sibling:
            raise UserError(self.env._(
                "The scans %(first)s and %(second)s both cover rule %(rule)s for %(company)s. "
                "Start them one after the other.",
                first=sibling.name, second=self.name, rule=shared_rule, company=shared_company))
        raise UserError(self.env._(
            "A scan of rule %(rule)s for %(company)s is already %(state)s (%(name)s). "
            "Wait for it to finish or cancel it.",
            rule=shared_rule, company=shared_company,
            state=dict(busy._fields['state'].selection)[busy.state].lower(), name=busy.name))

    # ---- batches ------------------------------------------------------------

    def _dgq_prepare_run(self):
        """First batch: decide which rules cannot run and open one checkpoint per model.

        Requirements: FR-DGQ-0020
        """
        self.ensure_one()
        not_run = {}
        runnable = self.env['data.quality.rule']
        for rule in self.rule_ids:
            reason = scan_service.not_run_reason(self.env, rule)
            if reason:
                not_run[str(rule.id)] = reason
            else:
                runnable |= rule
        total = 0
        for model in runnable.model_id:
            Model = self.env[model.model].sudo()  # sudo: scans read every record of the scope
            total += Model.search_count(scan_service.record_domain(
                Model, self.company_ids, 0, runnable.filtered(lambda rule: rule.model_id == model)))
        self.env['data.quality.scan.checkpoint'].create([
            {'scan_id': self.id, 'model_id': model.id} for model in runnable.model_id])
        self.write({'rules_not_run': not_run, 'total_count': total})

    def _dgq_run_batch(self):
        """Check one batch of records of the first model not finished; return (processed, remaining).

        Requirements: FR-DGQ-0015, FR-DGQ-0020
        """
        self.ensure_one()
        if not self.checkpoint_ids:
            self._dgq_prepare_run()
        pending = self.checkpoint_ids.filtered(lambda checkpoint: not checkpoint.done)
        if not pending:
            return 0, 0
        checkpoint = pending[0]
        processed = self._dgq_check_batch(checkpoint)
        left = len(pending) > 1 or not checkpoint.done
        remaining = max(self.total_count - self.done_count - processed, 1) if left else 0
        return processed, remaining

    def _dgq_check_batch(self, checkpoint):
        """Read one page of records of the checkpoint's model, check them and move the checkpoint.

        The batch starts after the checkpoint's last record, so a resumed scan never checks again a
        record already saved. Lines, tallies, counts and the checkpoint are written together.

        Requirements: FR-DGQ-0016, FR-DGQ-0017, FR-DGQ-0019
        """
        model_name = checkpoint.model_id.model
        rules = self.rule_ids.filtered(
            lambda rule: rule.model_id == checkpoint.model_id and str(rule.id) not in (self.rules_not_run or {}))
        started = time.monotonic()
        now = fields.Datetime.now()
        Model = self.env[model_name].sudo()  # sudo: scans read every record of the scope
        names = {'company_id'} & set(Model._fields)
        for rule in rules:
            names |= scan_service.rule_fields(rule)
        records = Model.search_fetch(
            scan_service.record_domain(Model, self.company_ids, checkpoint.last_id, rules),
            sorted(names), order='id', limit=JOB_BATCH_SIZE)
        lines, passed, failed, not_applicable = [], 0, 0, 0
        covered = set()
        results = {}
        for rule in rules:
            applicable = scan_service.applicable_records(rule, records)
            covered.update(applicable.ids)
            outcomes = validation.check(rule, applicable, now=now)
            results[rule] = outcomes
            judged = [outcome for outcome in outcomes.values() if outcome.status != validation.NOT_APPLICABLE]
            failing = {
                record_id: outcome for (record_id, _rule_id), outcome in outcomes.items()
                if outcome.status == validation.FAIL}
            passed += len(judged) - len(failing)
            failed += len(failing)
            not_applicable += len(records) - len(judged)
            lines.extend(
                self._dgq_line_values(rule, records.browse(res_id), outcome)
                for res_id, outcome in failing.items())
        self.env['data.quality.scan.line'].create(lines)
        counts = scan_service.batch_counts(
            rules, records, results, covered)
        self.env['data.quality.scan.tally']._dgq_add(self, checkpoint.model_id, counts)
        model_rows = [row for (_company, rule_id), row in counts.items() if not rule_id]
        last_id = records[-1:].id or checkpoint.last_id
        # a full batch may be the last one: look for a record after it, so no empty batch follows
        finished = len(records) < JOB_BATCH_SIZE or not Model.search_count(
            scan_service.record_domain(Model, self.company_ids, last_id, rules), limit=1)
        checkpoint.write({
            'last_id': last_id,
            'records_done': checkpoint.records_done + len(records),
            'done': finished,
        })
        self.write({
            'records_scanned': self.records_scanned + len(covered),
            'checks_passed': self.checks_passed + passed,
            'checks_failed': self.checks_failed + failed,
            'checks_not_applicable': self.checks_not_applicable + not_applicable,
            'records_error': self.records_error + sum(row['records_error'] for row in model_rows),
            'records_warning': self.records_warning + sum(row['records_warning'] for row in model_rows),
        })
        _logger.info(
            "dgq scan %s model %s batch up to id %s: %s records in %.2fs",
            self.id, model_name, checkpoint.last_id, len(records), time.monotonic() - started)
        return len(records)

    def _dgq_line_values(self, rule, record, outcome):
        """Values of the result line of one failed check."""
        return {
            'scan_id': self.id,
            'rule_id': rule.id,
            'rule_version': rule.version,
            'res_model': record._name,
            'res_id': record.id,
            'company_id': 'company_id' in record._fields and record.company_id.id or False,
            'severity': rule.severity,
            'explanation_code': outcome.code,
            'explanation_params': outcome.params,
        }
