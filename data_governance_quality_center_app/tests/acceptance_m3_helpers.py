"""Shared helpers of the acceptance tests of milestone M3 (T-014 to T-026).

Everything reaches models and fields through the registry, so a missing model or field makes a
test fail at run time, never at import. Forms (profile, import session, automation) are driven
with Odoo's ``Form`` helper with the fields the design names; buttons are the job mixin's
``action_start`` and the usual names of the other actions.
"""
import base64
import csv
import io

from odoo import SUPERUSER_ID, fields
from odoo.tests import Form

from .acceptance_m2b_helpers import M2BCase, call_first, choice, dump

__all__ = ['M3Case', 'call_first', 'choice', 'dump', 'csv_file', 'xlsx_file', 'as_pairs']

JOB_MODELS = (
    'data.quality.scan', 'data.quality.correction.run', 'data.quality.duplicate.check',
    'data.quality.profile', 'data.quality.import.session',
)


def csv_file(header, rows):
    """A CSV file (bytes) with a header line and the given rows."""
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(header)
    writer.writerows(rows)
    return out.getvalue().encode()


def xlsx_file(header, rows):
    """An XLSX file (bytes) with a header line and the given rows."""
    import xlsxwriter
    buffer = io.BytesIO()
    book = xlsxwriter.Workbook(buffer, {'in_memory': True, 'constant_memory': True})
    sheet = book.add_worksheet()
    sheet.write_row(0, 0, header)
    for index, row in enumerate(rows, start=1):
        sheet.write_row(index, 0, row)
    book.close()
    return buffer.getvalue()


def as_pairs(value):
    """A stored list of values with counts, whatever its layout, as ``{value: count}``.

    The design says a profile keeps frequencies and shapes with their counts as JSON, not the
    layout: a dict, a list of pairs or a list of small dicts are all read.
    """
    result = {}
    if isinstance(value, dict):
        for key, count in value.items():
            result[str(key)] = count
    elif isinstance(value, (list, tuple)):
        for item in value:
            if isinstance(item, dict):
                texts = [v for v in item.values() if isinstance(v, str)]
                numbers = [v for v in item.values() if isinstance(v, (int, float)) and not isinstance(v, bool)]
                if texts and numbers:
                    result[texts[0]] = numbers[-1]
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                result[str(item[0])] = item[1]
    return result


class M3Case(M2BCase):
    """Base class: users, isolation of a company's records, crons, job runner and notices."""

    def setUp(self):
        super().setUp()
        self.operator = self.user('operator', login='dgq_m3_operator', company=self.cx)
        self.viewer = self.user('viewer', login='dgq_m3_viewer', company=self.cx)
        for user in (self.operator, self.viewer, self.manager_a, self.administrator):
            user.write({'email': '%s@example.com' % user.login, 'notification_type': 'inbox'})

    # ---- records -----------------------------------------------------------

    def isolate(self, *model_names):
        """Move the records the setup left in company X (users' contacts and the like) to company Y.

        A figure of a scenario counts the records of the scenario only.
        """
        for name in model_names:
            table = self.env[name]._table
            self.env.cr.execute(
                "UPDATE %s SET company_id = %%s WHERE company_id = %%s" % table, (self.cy.id, self.cx.id))
            self.env[name].invalidate_model(['company_id'])

    def vendor_rule(self, severity='warning', name=None, **vals):
        """A rule that applies to the contacts marked as vendors (function DGQVEND) and wants a reference."""
        values = {
            'applies_domain': "[('function', '=', 'DGQVEND')]",
            'condition_domain': "[('ref', '!=', False)]",
            'severity': severity,
        }
        if name:
            values['name'] = name
        values.update(vals)
        return self.make_rule('res.partner', 'condition', **values)

    def vendors(self, count, prefix='Vendor'):
        return self.env['res.partner'].create([
            {'name': '%s %s' % (prefix, n), 'function': 'DGQVEND', 'company_id': self.cx.id}
            for n in range(1, count + 1)])

    def issues_of(self, rule, records=None):
        domain = [('rule_id', '=', rule.id)]
        if records is not None:
            domain.append(('res_id', 'in', records.ids))
        return self.env['data.quality.issue'].sudo().search(domain)

    # ---- jobs and scheduled actions ---------------------------------------

    def drain_all(self, rounds=40):
        """Run the scheduled action of every job kind until no job is queued."""
        for _ in range(rounds):
            busy = False
            for name in JOB_MODELS:
                if name not in self.env:
                    continue
                Model = self.env[name]
                if Model.sudo().search_count([('state', '=', 'queued')]):
                    Model._dgq_run_next()
                    busy = True
            if not busy:
                break
        self.env.invalidate_all()

    def fire_crons(self):
        """Run, as the scheduler does, the server action of every scheduled action of the app's models."""
        root = self.env['res.users'].browse(SUPERUSER_ID)
        crons = self.env['ir.cron'].sudo().search([('model_id.model', '=like', 'data.quality.%')])
        self.assertTrue(crons, "the app has no scheduled action")
        for cron in crons:
            cron.ir_actions_server_id.with_user(root).run()
        self.env.invalidate_all()

    def advance(self, rounds=3):
        """Let the schedulers and the job runners work: scheduled actions, then jobs, a few times."""
        for _ in range(rounds):
            self.fire_crons()
            self.drain_all()

    def make_due(self, automation):
        automation.sudo().write({'next_run': fields.Datetime.subtract(fields.Datetime.now(), hours=1)})

    def make_not_due(self, automation):
        automation.sudo().write({'next_run': fields.Datetime.add(fields.Datetime.now(), hours=5)})

    # ---- forms -------------------------------------------------------------

    def make_automation(self, name='Nightly contacts', user=None, rules=(), models=('res.partner',),
                        companies=None, trigger='schedule', **vals):
        """An automation filled in on its form, as an administrator does, and saved."""
        Automation = self.env['data.quality.automation'].with_user(user or self.administrator)
        companies = [self.cx] if companies is None else companies
        many = {name: vals.pop(name) for name in ('watched_field_ids', 'correction_ids') if vals.get(name)}
        vals = {key: value for key, value in vals.items() if value is not None}
        with Form(Automation) as form:
            form.name = name
            form.trigger = trigger
            if trigger == 'schedule':
                form.interval = vals.pop('interval', 'daily')
                form.hour = vals.pop('hour', 2)
            else:
                form.event = vals.pop('event', 'create')
                for field in many.get('watched_field_ids', ()):
                    form.watched_field_ids.add(field)
            for rule in rules:
                form.rule_ids.add(rule)
            for correction in many.get('correction_ids', ()):
                form.correction_ids.add(correction)
            for model_name in models:
                form.model_ids.add(self.model(model_name))
            for company in companies:
                form.company_ids.add(company)
            for key, value in vals.items():
                setattr(form, key, value)
        return form.record

    def make_team(self, name='Purchasing', leader=None, members=(), assignments=()):
        """A team; ``assignments`` is a list of ``(model name, user)``."""
        from odoo import Command
        return self.env['data.quality.team'].sudo().create({
            'name': name,
            'company_id': self.cx.id,
            'leader_id': leader.id if leader else False,
            'member_ids': [Command.set([user.id for user in members])],
            'assignment_ids': [
                Command.create({'model_id': self.model(model_name).id, 'user_id': user.id})
                for model_name, user in assignments],
        })

    def make_session(self, content, file_name, model_name='res.partner', mode='strict', user=None,
                     rules=(), duplicate_fields=(), company=None):
        """An import session filled in on its form with an uploaded file, and saved."""
        Session = self.env['data.quality.import.session'].with_user(user or self.operator)
        with Form(Session) as form:
            form.model_id = self.model(model_name)
            form.company_id = company or self.cx
            form.file = base64.b64encode(content).decode()
            form.file_name = file_name
            form.mode = mode
            for rule in rules:
                form.rule_ids.add(rule)
            for field in duplicate_fields:
                form.duplicate_field_ids.add(field)
        return form.record

    def check_file(self, session):
        """Run the check of a session in the background and return the finished session."""
        session.action_start()
        self.drain_job(session)
        session.invalidate_recordset()
        return session

    def make_profile(self, model_name, field_names, company=None, user=None, **vals):
        """A profile filled in on its form, as an operator does, and saved."""
        Profile = self.env['data.quality.profile'].with_user(user or self.operator)
        with Form(Profile) as form:
            form.model_id = self.model(model_name)
            form.company_id = company or self.cx
            for name in field_names:
                form.field_ids.add(self.fld(model_name, name))
            for key, value in vals.items():
                setattr(form, key, value)
        return form.record

    def run_profile(self, profile):
        """Start a profile and let the background runner compute it."""
        profile.action_start()
        self.drain_job(profile)
        profile.invalidate_recordset()
        return profile

    @staticmethod
    def line(profile, field_name):
        return profile.sudo().line_ids.filtered(lambda line: line.field_name == field_name)[:1]

    # ---- notices -----------------------------------------------------------

    def baseline(self):
        """The last ids of the notice tables: what is created later is new."""
        ids = {}
        for name in ('mail.message', 'mail.mail', 'mail.notification'):
            last = self.env[name].sudo().search([], order='id desc', limit=1)
            ids[name] = last.id or 0
        return ids

    def notices(self, user, since=None):
        """Number of messages, e-mails and inbox notifications created for a person after ``since``."""
        self.env.flush_all()
        since = since or {'mail.message': 0, 'mail.mail': 0, 'mail.notification': 0}
        partner = user.partner_id
        messages = self.env['mail.message'].sudo().search_count(
            [('id', '>', since['mail.message']), ('partner_ids', 'in', partner.ids)])
        mails = self.env['mail.mail'].sudo().search_count([
            ('id', '>', since['mail.mail']), '|',
            ('recipient_ids', 'in', partner.ids), ('email_to', 'ilike', user.email or '@@@')])
        notifications = self.env['mail.notification'].sudo().search_count(
            [('id', '>', since['mail.notification']), ('res_partner_id', '=', partner.id)])
        return messages + mails + notifications

    def mails_to(self, user, since=None):
        since = since or {'mail.mail': 0}
        return self.env['mail.mail'].sudo().search([
            ('id', '>', since['mail.mail']), '|',
            ('recipient_ids', 'in', user.partner_id.ids), ('email_to', 'ilike', user.email or '@@@')])
