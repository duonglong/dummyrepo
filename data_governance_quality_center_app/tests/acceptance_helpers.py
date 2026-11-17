"""Shared helpers of the M2 acceptance tests (T-005 to T-012).

Everything here reaches models and fields through the registry, so a missing model or field
makes a test fail at run time, never at import. The helpers use only the names of the design
(models, fields, the job mixin's ``action_start``, ``action_resume`` and ``_dgq_run_next``).
"""
import json
from unittest.mock import patch

from odoo import Command, fields
from odoo.tests import new_test_user

from .common import DGQTestCase

MODULE = 'data_governance_quality_center_app'
DONE_STATES = ('done', 'failed', 'cancelled')


def choice(env, model, field, include, exclude=(), keys=()):
    """Key of the selection value of ``model.field`` that means ``include`` words.

    The design names the choices in words, not in keys: a value is found by its key (when
    ``keys`` names it) or by the words of its label.
    """
    selection = env[model]._fields[field]._description_selection(env)
    for key, _label in selection:
        if key in keys:
            return key
    for key, label in selection:
        text = ('%s %s' % (key, label)).lower().replace('_', ' ')
        if all(word in text for word in include) and not any(word in text for word in exclude):
            return key
    raise AssertionError("No choice of %s.%s means %s: %s" % (model, field, include, selection))


def dump(value):
    """Lower-case JSON text of any value, for a loose look at stored structures."""
    return json.dumps(value, default=str, sort_keys=True).lower()


class AcceptanceCase(DGQTestCase):
    """Base class: two companies, manual test fields, users and a scan runner."""

    _seq = 0

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Company = cls.env['res.company']
        cls.cx = Company.create({'name': 'DGQ Acceptance X'})
        cls.cy = Company.create({'name': 'DGQ Acceptance Y'})
        cls.ensure_field('res.partner', 'x_dgq_born_on', 'date', 'DGQ Born On')
        cls.ensure_field('res.partner', 'x_dgq_list', 'many2one', 'DGQ Price List',
                         relation='product.pricelist')
        cls.ensure_field('product.template', 'x_dgq_barcode', 'char', 'DGQ Barcode')
        cls.ensure_field('product.template', 'x_dgq_cost', 'float', 'DGQ Cost')
        cls.ensure_field('product.template', 'x_dgq_start', 'date', 'DGQ Start')
        cls.ensure_field('product.template', 'x_dgq_end', 'date', 'DGQ End')

    @classmethod
    def ensure_field(cls, model_name, name, ttype, label, relation=None):
        model = cls.env['ir.model']._get(model_name)
        found = cls.env['ir.model.fields'].search([('model', '=', model_name), ('name', '=', name)])
        if found:
            return found
        vals = {
            'name': name, 'model': model_name, 'model_id': model.id,
            'field_description': label, 'ttype': ttype, 'state': 'manual',
        }
        if relation:
            vals['relation'] = relation
        return cls.env['ir.model.fields'].create(vals)

    def setUp(self):
        super().setUp()
        # A test cannot commit: the runner's commit points are replaced, as in the job engine tests.
        cron = type(self.env['ir.cron'])
        for name, options in (('_commit_progress', {'return_value': float('inf')}), ('_rollback_progress', {})):
            patcher = patch.object(cron, name, **options)
            patcher.start()
            self.addCleanup(patcher.stop)

    # ---- users and lookups -------------------------------------------------

    def user(self, level, login=None, company=None):
        """A test user of an access level (viewer, operator, manager, admin)."""
        user = new_test_user(
            self.env, login=login or 'dgq_m2_%s' % level,
            groups='base.group_user,%s.group_dgq_%s' % (MODULE, level))
        if company:
            user.write({'company_ids': [Command.link(company.id)], 'company_id': company.id})
        return user

    def model(self, name):
        return self.env['ir.model']._get(name)

    def fld(self, model_name, name):
        return self.env['ir.model.fields']._get(model_name, name)

    # ---- records -----------------------------------------------------------

    def partner(self, name, company=None, **vals):
        return self.env['res.partner'].create(dict(vals, name=name, company_id=(company or self.cx).id))

    def product(self, name, company=None, **vals):
        return self.env['product.template'].create(dict(vals, name=name, company_id=(company or self.cx).id))

    def make_rule(self, model_name, rule_type, field_names=(), company=None, **vals):
        """Create a rule restricted to one company (``cx`` unless ``company`` is False)."""
        AcceptanceCase._seq += 1
        field_ids = [self.fld(model_name, name).id for name in field_names]
        if company is None:
            company = self.cx
        values = {
            'name': 'DGQ rule %s' % AcceptanceCase._seq,
            'model_id': self.model(model_name).id,
            'rule_type': rule_type,
            'field_ids': [Command.set(field_ids)],
            'severity': 'warning',
            'company_ids': [Command.set([company.id] if company else [])],
        }
        values.update(vals)
        return self.env['data.quality.rule'].create(values)

    # ---- scans -------------------------------------------------------------

    def make_scan(self, rules=(), companies=None, models=(), user=None):
        Scan = self.env['data.quality.scan']
        if user:
            Scan = Scan.with_user(user)
        companies = [self.cx] if companies is None else companies
        return Scan.create({
            'rule_ids': [Command.set([rule.id for rule in rules])],
            'company_ids': [Command.set([company.id for company in companies])],
            'model_ids': [Command.set([self.model(name).id for name in models])],
        })

    def drain(self, scan, max_runs=30):
        """Run the scheduled action until the scan has ended (it is the real runner)."""
        for _ in range(max_runs):
            scan.invalidate_recordset()
            if scan.sudo().state in DONE_STATES:
                break
            self.env['data.quality.scan']._dgq_run_next()
        scan.invalidate_recordset()
        return scan

    def run_scan(self, rules=(), companies=None, models=(), user=None):
        """Create a scan, start it and let the background runner finish it."""
        scan = self.make_scan(rules, companies, models, user)
        scan.action_start()
        return self.drain(scan)

    def failing(self, scan, rule=None):
        """Result lines of a scan by record id (one failed check per record and rule)."""
        return {
            line.res_id: line for line in scan.sudo().line_ids
            if rule is None or line.rule_id == rule
        }

    def text(self, line):
        return line.sudo().explanation or ''

    def day(self, offset=0):
        return fields.Date.add(fields.Date.today(), days=offset)

    def set_write_date(self, record, days_ago):
        """Make a record last updated ``days_ago`` days ago at midday."""
        moment = fields.Datetime.add(
            fields.Datetime.to_datetime(fields.Date.today()), days=-days_ago, hours=12)
        self.env.cr.execute(
            "UPDATE %s SET write_date = %%s WHERE id = %%s" % record._table, (moment, record.id))
        record.invalidate_recordset(['write_date'])
        return moment
