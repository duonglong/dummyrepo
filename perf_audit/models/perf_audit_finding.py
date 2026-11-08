import functools
import logging
import re

import psycopg2

from odoo import _, api, fields, models, sql_db
from odoo.exceptions import UserError
from odoo.modules import module as odoo_module

_logger = logging.getLogger(__name__)

QUERY_TEXT_LIMIT = 500
AUTOVACUUM_SETTING = re.compile(r'autovacuum_[a-z_]+')
SEVERITY_RANK = {'high': 1, 'medium': 2, 'low': 3}


def _quote(name):
    """Quote an SQL identifier."""
    return '"%s"' % str(name).replace('"', '""')


def _readable(value):
    """One value as plain text: a list joins its items, a mapping reads as key=value pairs."""
    if isinstance(value, dict):
        return ', '.join('%s=%s' % (key, _readable(item)) for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return '; '.join(_readable(item) for item in value) if any(
            isinstance(item, (dict, list, tuple)) for item in value) else ', '.join(map(str, value))
    return str(value)


def _labelled_lines(value):
    """A JSON value as one labelled line per key; a bare list gives one line per item."""
    if not value:
        return False
    if not isinstance(value, dict):
        return '\n'.join(_readable(item) for item in value) if isinstance(value, (list, tuple)) else _readable(value)
    return '\n'.join(
        '%s: %s' % (key.replace('_', ' ').capitalize(), _readable(item)) for key, item in value.items())


class PerfAuditFinding(models.Model):
    """One problem a check found in a run, with its evidence and its proposed fix.

    The fix is stored as a kind plus names and the statement is rebuilt from them,
    so only the safe statements of BR-OPA-0004 can ever exist.

    Requirements: FR-OPA-0026, FR-OPA-0027, FR-OPA-0029, NFR-OPA-0005, BR-OPA-0002, BR-OPA-0004
    """

    _name = 'perf.audit.finding'
    _description = 'Performance audit finding'
    _rec_name = 'message'
    _order = 'code, severity_rank, check_rank, identity_key'

    run_id = fields.Many2one('perf.audit.run', required=True, index=True, ondelete='cascade')
    check_id = fields.Many2one('perf.audit.check', required=True, ondelete='restrict')
    code = fields.Char(related='check_id.code', store=True)
    kind = fields.Char(help='Part of a check, for example unused or duplicate for A3.')
    identity_key = fields.Char(required=True, index=True)
    severity = fields.Selection(
        [('high', 'High'), ('medium', 'Medium'), ('low', 'Low')], required=True)
    severity_rank = fields.Integer(compute='_compute_severity_rank', store=True)
    check_rank = fields.Integer(help='Position in the list the check returned.')
    message = fields.Char(required=True)
    # evidence
    table_name = fields.Char()
    row_estimate = fields.Integer(help='An estimate from the database statistics.')
    evidence = fields.Json()
    threshold_label = fields.Char()
    threshold_value = fields.Float()
    found_value = fields.Float()
    stats_reset_date = fields.Datetime()
    query_id = fields.Char()
    query_text = fields.Text()
    # Odoo reference
    model_id = fields.Many2one('ir.model', ondelete='set null')
    field_id = fields.Many2one('ir.model.fields', ondelete='set null')
    index_name = fields.Char()
    view_id = fields.Many2one('ir.ui.view', ondelete='set null')
    rule_id = fields.Many2one('ir.access', ondelete='set null')
    cron_id = fields.Many2one('ir.cron', ondelete='set null')
    references = fields.Json()
    evidence_text = fields.Text(compute='_compute_evidence_text')
    references_text = fields.Text(compute='_compute_evidence_text')
    # fix
    fix_kind = fields.Selection([
        ('create_index', 'Create index'), ('drop_index', 'Drop index'),
        ('analyze', 'Analyze'), ('vacuum', 'Vacuum'), ('autovacuum', 'Tune autovacuum'),
    ])
    fix_params = fields.Json()
    fix_statement = fields.Text(compute='_compute_fix_statement')
    code_suggestion = fields.Text()
    # comparison and apply
    comparison_state = fields.Selection(
        [('new', 'New'), ('open', 'Open'), ('regressed', 'Regressed'), ('fixed', 'Fixed')],
        required=True, default='new')
    apply_state = fields.Selection(
        [('none', 'Not applied'), ('applied', 'Applied'), ('failed', 'Failed')],
        required=True, default='none')
    applied_by = fields.Many2one('res.users', ondelete='set null')
    applied_date = fields.Datetime()
    apply_error = fields.Text()
    can_apply = fields.Boolean(compute='_compute_can_apply')

    _identity_unique = models.Constraint(
        'UNIQUE(run_id, check_id, identity_key)', 'A finding is identified once per run and check.')
    _query_text_limit = models.Constraint(
        'CHECK(char_length(query_text) <= %s)' % QUERY_TEXT_LIMIT,
        'A stored query text has at most %s characters.' % QUERY_TEXT_LIMIT)

    @api.depends('severity')
    def _compute_severity_rank(self):
        """Rank the severity so the worst problems sort first.

        Requirements: BR-OPA-0002
        """
        for finding in self:
            finding.severity_rank = SEVERITY_RANK.get(finding.severity, len(SEVERITY_RANK) + 1)

    @api.depends('evidence', 'references')
    def _compute_evidence_text(self):
        """Show the evidence and the other places as labelled lines instead of raw JSON.

        Requirements: FR-OPA-0026
        """
        for finding in self:
            finding.evidence_text = _labelled_lines(finding.evidence)
            finding.references_text = _labelled_lines(finding.references)

    @api.depends('fix_kind', 'fix_params')
    def _compute_fix_statement(self):
        """Build the statement of the fix from its kind and names, with quoted identifiers.

        A kind outside the safe list, or missing names, builds no statement.

        Requirements: FR-OPA-0027
        """
        for finding in self:
            finding.fix_statement = finding._build_fix_statement()

    def _build_fix_statement(self):
        """Statement text of this finding's fix, or an empty string."""
        self.ensure_one()
        params = self.fix_params or {}
        table, index_name = params.get('table'), params.get('index_name')
        match self.fix_kind:
            case 'create_index' if table and index_name and params.get('columns'):
                columns = ', '.join(_quote(column) for column in params['columns'])
                return 'CREATE INDEX CONCURRENTLY %s ON %s (%s)' % (
                    _quote(index_name), _quote(table), columns)
            case 'drop_index' if index_name:
                return 'DROP INDEX CONCURRENTLY %s' % _quote(index_name)
            case 'analyze' if table:
                return 'ANALYZE %s' % _quote(table)
            case 'vacuum' if table:
                return 'VACUUM (ANALYZE) %s' % _quote(table)
            case 'autovacuum' if table and self._valid_autovacuum_settings(params.get('settings')):
                settings = ', '.join(
                    '%s = %s' % (key, float(value)) for key, value in sorted(params['settings'].items()))
                return 'ALTER TABLE %s SET (%s)' % (_quote(table), settings)
        return ''

    @staticmethod
    def _valid_autovacuum_settings(settings):
        """True for a non-empty mapping of autovacuum storage parameters to numbers."""
        return bool(settings) and all(
            AUTOVACUUM_SETTING.fullmatch(key) and isinstance(value, (int, float))
            for key, value in settings.items())

    @api.depends('fix_statement', 'apply_state', 'comparison_state', 'run_id.is_latest')
    def _compute_can_apply(self):
        """A finding offers Apply when its run is the latest, it has a statement and it was not applied.

        Requirements: FR-OPA-0029
        """
        for finding in self:
            finding.can_apply = (
                bool(finding.fix_statement)
                and finding.apply_state != 'applied'
                and finding.comparison_state != 'fixed'
                and finding.run_id.is_latest)

    def action_apply(self):
        """Open the confirmation that shows the statement before it runs.

        Requirements: FR-OPA-0029
        """
        self.ensure_one()
        self._check_can_apply()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Apply the fix'),
            'res_model': 'perf.audit.apply.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_finding_id': self.id},
        }

    def _check_can_apply(self):
        """Refuse a finding that offers no Apply."""
        if not self.can_apply:
            raise UserError(_('This finding cannot be applied.'))

    def _apply_fix(self):
        """Run the statement of this finding on its own and record who, when and the outcome.

        A failing statement records its database error and changes nothing else. A concurrent
        index build waits for every older snapshot of the database, including the one of this
        request, so outside a test the statement runs once the request has committed.

        Requirements: FR-OPA-0029, BR-OPA-0004
        """
        self.ensure_one()
        self._check_can_apply()
        if odoo_module.current_test:
            self._record_apply_outcome(self._execute_in_savepoint(
                self.fix_statement.replace(' CONCURRENTLY', '')))
            return
        self.env.cr.postcommit.add(functools.partial(
            self._apply_after_commit, self.env.cr.dbname, self.id, self.env.uid, self.fix_statement))

    @api.model
    def _apply_after_commit(self, dbname, finding_id, uid, statement):
        """Run the statement of a committed request, then record its outcome on a fresh cursor.

        Requirements: FR-OPA-0029
        """
        error = self._run_statement_alone(dbname, statement)
        with self.pool.cursor() as cr:
            finding = api.Environment(cr, uid, {})['perf.audit.finding'].browse(finding_id)
            finding._record_apply_outcome(error)

    @staticmethod
    def _run_statement_alone(dbname, statement):
        """Run one statement on its own autocommit connection; return the database error or None."""
        with sql_db.db_connect(dbname).cursor() as cr:
            cr._cnx.autocommit = True
            try:
                cr.execute(statement)
            except psycopg2.Error as error:
                return str(error)
        return None

    def _record_apply_outcome(self, error):
        """Record who applied the fix, when, and its outcome; a failure keeps its database error."""
        self.write({
            'apply_state': 'failed' if error else 'applied',
            'apply_error': error or False,
            'applied_by': self.env.uid,
            'applied_date': fields.Datetime.now(),
        })
        _logger.info('Fix of finding %s %s', self.identity_key, 'failed' if error else 'applied')

    def _execute_in_savepoint(self, statement):
        """Run the statement in a savepoint of the current cursor; return the error or None."""
        try:
            with self.env.cr.savepoint():
                self.env.cr.execute(statement)
        except psycopg2.Error as error:
            return str(error)
        return None

    @api.model
    def _cut_query_text(self, vals):
        """Keep at most the first QUERY_TEXT_LIMIT characters of a query text.

        Requirements: NFR-OPA-0005
        """
        if vals.get('query_text'):
            vals['query_text'] = vals['query_text'][:QUERY_TEXT_LIMIT]
        return vals

    @api.model_create_multi
    def create(self, vals_list):
        """Create findings, cutting the query text.

        Requirements: NFR-OPA-0005
        """
        return super().create([self._cut_query_text(dict(vals)) for vals in vals_list])

    def write(self, vals):
        """Write findings, cutting the query text.

        Requirements: NFR-OPA-0005
        """
        return super().write(self._cut_query_text(dict(vals)))
