import re

from odoo import api, fields, models

QUERY_TEXT_LIMIT = 500
AUTOVACUUM_SETTING = re.compile(r'autovacuum_[a-z_]+')
SEVERITY_RANK = {'high': 1, 'medium': 2, 'low': 3}


def _quote(name):
    """Quote an SQL identifier."""
    return '"%s"' % str(name).replace('"', '""')


class PerfAuditFinding(models.Model):
    """One problem a check found in a run, with its evidence and its proposed fix.

    The fix is stored as a kind plus names and the statement is rebuilt from them,
    so only the safe statements of BR-OPA-0004 can ever exist.

    Requirements: FR-OPA-0026, FR-OPA-0027, NFR-OPA-0005, BR-OPA-0002
    """

    _name = 'perf.audit.finding'
    _description = 'Performance audit finding'
    _order = 'code, severity_rank, identity_key'

    run_id = fields.Many2one('perf.audit.run', required=True, index=True, ondelete='cascade')
    check_id = fields.Many2one('perf.audit.check', required=True, ondelete='restrict')
    code = fields.Char(related='check_id.code', store=True)
    kind = fields.Char(help='Part of a check, for example unused or duplicate for A3.')
    identity_key = fields.Char(required=True, index=True)
    severity = fields.Selection(
        [('high', 'High'), ('medium', 'Medium'), ('low', 'Low')], required=True)
    severity_rank = fields.Integer(compute='_compute_severity_rank', store=True)
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
        'UNIQUE(run_id, identity_key)', 'A finding is identified once per run.')
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

    @api.depends('fix_statement', 'apply_state')
    def _compute_can_apply(self):
        """A finding can be applied when it has a statement that was not applied yet."""
        for finding in self:
            finding.can_apply = bool(finding.fix_statement) and finding.apply_state != 'applied'

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
