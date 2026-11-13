import logging
import time

from odoo import api, fields, models
from odoo.exceptions import UserError
from odoo.fields import Domain
from odoo.tools import SQL

from ..const import JOB_BATCH_SIZE
from ..services import scan as scan_service

_logger = logging.getLogger(__name__)


class DataQualityScan(models.Model):
    """One run of rules over records: scope, state, progress and counts.

    Requirements: FR-DGQ-0015, FR-DGQ-0018, FR-DGQ-0020
    """
    _name = 'data.quality.scan'
    _inherit = ['data.quality.job.mixin']
    _description = 'Data Quality Scan'
    _order = 'id desc'
    _dgq_cron_xmlid = 'data_governance_quality_center_app.ir_cron_dgq_scan'

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

    line_ids = fields.One2many('data.quality.scan.line', 'scan_id', string='Results')
    checkpoint_ids = fields.One2many('data.quality.scan.checkpoint', 'scan_id', string='Checkpoints')

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

        An operator may name a company of the scan that his own companies do not list, which
        Odoo would refuse as a read of that company; the scan records the choice, the access to
        the results stays limited by company (BR-DGQ-0011).
        """
        # sudo: an operator may create a scan without reading the sequence
        sequence = self.env['ir.sequence'].sudo()
        vals_list = [dict(vals) for vals in vals_list]
        companies = []
        for vals in vals_list:
            companies.append(vals.pop('company_ids', None))
            if vals.get('name', '/') == '/':
                vals['name'] = sequence.next_by_code('data.quality.scan') or '/'
        scans = super().create(vals_list)
        for scan, commands in zip(scans, companies):
            if commands:
                scan.sudo().write({'company_ids': commands})  # sudo: companies the operator may not read
        return scans

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

        Requirements: FR-DGQ-0015, FR-DGQ-0018
        """
        self.check_access('write')
        for scan in self:
            scan._dgq_resolve_scope()
        self._dgq_lock_scope_rules()
        for scan in self:
            scan._dgq_refuse_overlap()
        return super()._dgq_enqueue()

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
        # sudo: the scope is resolved over every rule and company whatever the requester may read
        Rule = self.env['data.quality.rule'].sudo()
        companies = self.sudo().company_ids or self.env.user.sudo().company_ids  # sudo: the scope covers companies the requester may not read
        rules = self.rule_ids.sudo()  # sudo: the requester may not read every rule
        if rules:
            rules = rules.filtered('active')
        else:
            rules = Rule.search([('active', '=', True)]).filtered(
                lambda rule: self.env['data.quality.model.config']._get_status(rule.model_name)
                in ('supported', 'restricted'))
        if self.model_ids:
            rules = rules.filtered(lambda rule: rule.model_id in self.model_ids)
        rules = rules.filtered(lambda rule: not rule.company_ids or rule.company_ids & companies)
        if not rules:
            raise UserError(self.env._("There is no active rule to scan in this scope."))
        self.sudo().write({  # sudo: the app writes the resolved scope
            'rule_ids': [fields.Command.set(rules.ids)],
            'model_ids': [fields.Command.set(rules.model_id.ids)],
            'company_ids': [fields.Command.set(companies.ids)],
        })

    def _dgq_refuse_overlap(self):
        """Refuse a scan that shares a rule and a company with a queued or running scan.

        Requirements: FR-DGQ-0018
        """
        self.ensure_one()
        # sudo: the running scan of another user still blocks this one
        busy = self.sudo().search([
            ('id', '!=', self.id),
            ('state', 'in', ('queued', 'running')),
            ('rule_ids', 'in', self.rule_ids.ids),
            ('company_ids', 'in', self.company_ids.ids),
        ], limit=1)
        if busy:
            # sudo: the shared rule and company may not be readable by the requester
            shared_rule = (busy.rule_ids & self.sudo().rule_ids)[:1]
            shared_company = (busy.company_ids & self.sudo().company_ids)[:1]
            raise UserError(self.env._(
                "A scan of rule %(rule)s for %(company)s is already %(state)s (%(name)s). "
                "Wait for it to finish or cancel it.",
                rule=shared_rule.display_name, company=shared_company.display_name,
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
            total += Model.search_count(scan_service.record_domain(Model, self.company_ids, 0))
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
        """Read one page of records of the checkpoint's model, check them and move the checkpoint."""
        model_name = checkpoint.model_id.model
        rules = self.rule_ids.filtered(
            lambda rule: rule.model_id == checkpoint.model_id and str(rule.id) not in (self.rules_not_run or {}))
        started = time.monotonic()
        Model = self.env[model_name].sudo()  # sudo: scans read every record of the scope
        names = {'company_id'} & set(Model._fields)
        for rule in rules:
            names |= scan_service.rule_fields(rule)
        records = Model.search_fetch(
            scan_service.record_domain(Model, self.company_ids, checkpoint.last_id),
            sorted(names), order='id', limit=JOB_BATCH_SIZE)
        lines, passed, failed, not_applicable = [], 0, 0, 0
        for rule in rules:
            failing, applicable = scan_service.check_required(rule, records)
            passed += len(applicable) - len(failing)
            failed += len(failing)
            not_applicable += len(records) - len(applicable)
            lines.extend(
                self._dgq_line_values(rule, records.browse(res_id), empty)
                for res_id, empty in failing.items())
        self.env['data.quality.scan.line'].create(lines)
        checkpoint.write({
            'last_id': records[-1:].id or checkpoint.last_id,
            'records_done': checkpoint.records_done + len(records),
            'done': len(records) < JOB_BATCH_SIZE,
        })
        self.write({
            'records_scanned': self.records_scanned + len(records),
            'checks_passed': self.checks_passed + passed,
            'checks_failed': self.checks_failed + failed,
            'checks_not_applicable': self.checks_not_applicable + not_applicable,
        })
        _logger.info(
            "dgq scan %s model %s batch up to id %s: %s records in %.2fs",
            self.id, model_name, checkpoint.last_id, len(records), time.monotonic() - started)
        return len(records)

    def _dgq_line_values(self, rule, record, empty_fields):
        """Values of the result line of one failed check."""
        return {
            'scan_id': self.id,
            'rule_id': rule.id,
            'rule_version': rule.version,
            'res_model': record._name,
            'res_id': record.id,
            'company_id': 'company_id' in record._fields and record.company_id.id or False,
            'severity': rule.severity,
            'explanation_code': 'required_missing',
            'explanation_params': {'fields': empty_fields},
        }


class DataQualityScanCheckpoint(models.Model):
    """Where a scan stands in one model.

    Requirements: FR-DGQ-0015
    """
    _name = 'data.quality.scan.checkpoint'
    _description = 'Data Quality Scan Checkpoint'

    scan_id = fields.Many2one('data.quality.scan', required=True, ondelete='cascade', index=True)
    model_id = fields.Many2one('ir.model', required=True, ondelete='cascade')
    last_id = fields.Integer()
    records_done = fields.Integer()
    done = fields.Boolean()

    @api.model
    def _access_domain(self, operation):
        """A checkpoint is as visible as its scan, which is separated by company.

        Requirements: FR-DGQ-0071, BR-DGQ-0011
        """
        domain = super()._access_domain(operation)
        if operation == 'create':
            return domain
        return domain & Domain('scan_id', 'any', [])

    _scan_model_unique = models.Constraint(
        'UNIQUE(scan_id, model_id)', 'A scan has one checkpoint per model.')


class DataQualityScanLine(models.Model):
    """One failed check of one record in one scan.

    Requirements: FR-DGQ-0015
    """
    _name = 'data.quality.scan.line'
    _inherit = ['data.quality.result.mixin']
    _description = 'Data Quality Scan Result'

    scan_id = fields.Many2one('data.quality.scan', required=True, ondelete='cascade', index=True)
    rule_id = fields.Many2one('data.quality.rule', required=True, ondelete='restrict')
    rule_version = fields.Integer()
    res_id = fields.Many2oneReference(string='Record', model_field='res_model', index=True)
    severity = fields.Selection([
        ('info', 'Information'),
        ('warning', 'Warning'),
        ('critical', 'Critical'),
    ])
    explanation_code = fields.Char()
    explanation_params = fields.Json()
    explanation = fields.Text(compute='_compute_explanation')

    @api.depends('explanation_code', 'explanation_params', 'res_model')
    def _compute_explanation(self):
        """Render the stored code in the reader's language."""
        for line in self:
            params = line.explanation_params or {}
            if line.explanation_code == 'required_missing':
                model = self.env.registry.get(line.res_model)
                labels = [
                    model._fields[name]._description_string(self.env) if model and name in model._fields
                    else name
                    for name in params.get('fields', [])]
                line.explanation = self.env._("A value is required for: %s", ", ".join(labels))
            else:
                line.explanation = line.explanation_code or False
