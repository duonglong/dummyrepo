"""Shared helpers of the M2 acceptance tests (T-003 to T-010).

Requirements: FR-OPA-0001, BR-OPA-0001
"""
import json

from odoo import fields
from odoo.tests.common import TransactionCase

CHECK_CODES = [
    'A1', 'A2', 'A3', 'A4', 'A5', 'A6', 'A7', 'A8',
    'B1', 'B2', 'B3', 'B4', 'B5', 'B6', 'B7',
]
# The spec names no method to start a run; these are the names tried, in order.
START_METHODS = (
    'action_start_audit', 'action_start', 'start_audit', 'action_run_audit',
    'run_audit', 'action_run', 'start_run',
)
HUGE = 10 ** 12


class AuditAcceptanceCase(TransactionCase):
    """A system administrator and the helpers to start a run and read its output."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.audit_admin = cls.env['res.users'].create({
            'name': 'M2 Acceptance Administrator',
            'login': 'm2_acceptance_admin',
            'group_ids': [(6, 0, [cls.env.ref('base.group_system').id])],
        })
        cls.audit_plain = cls.env['res.users'].create({
            'name': 'M2 Acceptance Plain User',
            'login': 'm2_acceptance_plain',
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id])],
        })

    # -- runs ---------------------------------------------------------------

    def start_run(self, user=None):
        """Start an audit as the administrator and return the new run."""
        Run = self.env['perf.audit.run'].with_user(user or self.audit_admin)
        before = Run.sudo().search([]).ids
        for method in START_METHODS:
            if hasattr(Run, method):
                getattr(Run, method)()
                break
        else:
            self.fail('perf.audit.run has no method to start a run (tried %s)' % ', '.join(START_METHODS))
        self.env.flush_all()
        run = Run.sudo().search([('id', 'not in', before)], order='id desc', limit=1)
        self.assertTrue(run, 'starting an audit must create a run')
        return run

    def set_threshold(self, key, value, check=None):
        """Tune one threshold; `check` is a check code, or None for a global one."""
        domain = [('key', '=', key)]
        if check:
            domain.append(('check_id.code', '=', check))
        else:
            domain.append(('check_id', '=', False))
        threshold = self.env['perf.audit.threshold'].sudo().search(domain)
        self.assertEqual(len(threshold), 1, 'threshold %s/%s must exist once' % (check, key))
        threshold.value = value

    def result(self, run, code):
        res = run.sudo().check_result_ids.filtered(lambda r: r.check_id.code == code)
        self.assertEqual(len(res), 1, 'check %s must have one result line in the run' % code)
        return res

    def findings(self, run, code, kind=None):
        found = run.sudo().finding_ids.filtered(lambda f: f.check_id.code == code)
        if kind:
            found = found.filtered(lambda f: f.kind == kind)
        return found

    def dump(self, finding):
        """Everything a finding stores about its evidence, as one lowercase text."""
        parts = [finding.message or '', finding.index_name or '', finding.table_name or '',
                 json.dumps(finding.evidence or {}, default=str),
                 json.dumps(finding.references or {}, default=str)]
        return ' '.join(parts).lower()

    def now(self):
        return fields.Datetime.now()

    # -- database objects ---------------------------------------------------

    def set_reltuples(self, table, rows):
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE pg_class SET reltuples = %s WHERE oid = to_regclass(%s)", [rows, table])

    def drop_plain_indexes(self, table):
        """Drop every index of the table except primary key and unique ones."""
        cr = self.env.cr
        cr.execute("""
            SELECT i.indexrelid::regclass::text FROM pg_index i
             WHERE i.indrelid = to_regclass(%s) AND NOT i.indisprimary AND NOT i.indisunique
        """, [table])
        for (name,) in cr.fetchall():
            cr.execute('DROP INDEX %s' % name)

    def make_model(self, model, order='id'):
        rec = self.env['ir.model'].create({
            'name': model.replace('_', ' ').title(), 'model': model, 'order': order})
        return rec

    def add_field(self, model, name, ttype, **kw):
        vals = {
            'name': name, 'field_description': name, 'ttype': ttype,
            'model_id': model.id, 'state': 'manual',
        }
        vals.update(kw)
        return self.env['ir.model.fields'].create(vals)

    def make_x_line(self, rows=None, order='id'):
        """Custom model x_line (table x_line): x_partner_id many2one, x_date, x_state."""
        model = self.make_model('x_line')
        self.add_field(model, 'x_partner_id', 'many2one', relation='res.partner',
                       on_delete='set null', index=False)
        self.add_field(model, 'x_date', 'date')
        self.add_field(model, 'x_state', 'char')
        if order != 'id':
            model.order = order
        self.drop_plain_indexes('x_line')
        if rows is not None:
            self.set_reltuples('x_line', rows)
        return model
