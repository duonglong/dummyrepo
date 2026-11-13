"""Shared helpers of the acceptance tests of T-013 and T-016 to T-020.

Everything reaches models and fields through the registry, so a missing model or field makes a test
fail at run time, never at import. The design names the fields of the dialogs and the models but not
the method behind every button: ``call_first`` looks a button up under the usual names of its action
and fails with the list of names when none exists.
"""
import re

from odoo import Command
from odoo.tests import Form

from .acceptance_helpers import DONE_STATES, AcceptanceCase, choice, dump

__all__ = ['M2BCase', 'call_first', 'choice', 'dump']


def call_first(record, names, *args, **kwargs):
    """Call the first method of ``record`` found under one of ``names`` and return its answer."""
    for name in names:
        method = getattr(record, name, None)
        if callable(method):
            return method(*args, **kwargs)
    raise AssertionError("%s has no button method among %s" % (record._name, list(names)))


DRY_RUN = ('action_dry_run', 'action_preview', 'action_run_dry', 'dry_run')
REAL_RUN = ('action_run', 'action_run_for_real', 'action_apply', 'action_execute', 'action_start_run')
APPROVE = ('action_approve', 'approve')
ROLLBACK = ('action_rollback', 'rollback', 'action_undo')
CONFIRM = ('action_confirm', 'action_merge', 'action_apply', 'confirm')
DISMISS = ('action_dismiss', 'action_set_aside', 'action_not_duplicates', 'action_not_duplicate',
           'action_mark_not_duplicates')
BULK_APPLY = ('action_apply', 'action_confirm', 'action_execute', 'action_run', 'action_assign', 'apply')


def words_in(text, *words):
    text = (text or '').lower()
    return all(word in text for word in words)


class M2BCase(AcceptanceCase):
    """Base class: duplicate rules and checks, merge previews, corrections and their runs."""

    # ---- jobs --------------------------------------------------------------

    def drain_job(self, records, max_runs=60):
        """Let the real runner work on a job model until the given jobs have ended."""
        if not records or 'state' not in records._fields:
            return records
        Model = self.env[records._name]
        for _ in range(max_runs):
            records.invalidate_recordset()
            if all(job.sudo().state in DONE_STATES for job in records):
                break
            Model._dgq_run_next()
        records.invalidate_recordset()
        return records

    # ---- duplicate rules and checks ----------------------------------------

    def dup_rule(self, model_name, spec, threshold=85, company=None, name=None, **vals):
        """A duplicate rule on ``model_name``: ``spec`` is a list of (field, method, weight)."""
        M2BCase._seq += 1
        if company is None:
            company = self.cx
        values = {
            'name': name or 'DGQ duplicate rule %s' % M2BCase._seq,
            'model_id': self.model(model_name).id,
            'threshold': threshold,
            'company_id': company.id if company else False,
            'field_line_ids': [
                Command.create({
                    'sequence': index, 'field_id': self.fld(model_name, field).id,
                    'method': method, 'weight': weight})
                for index, (field, method, weight) in enumerate(spec)],
        }
        values.update(vals)
        return self.env['data.quality.duplicate.rule'].create(values)

    def contact_rule(self, threshold=85, **vals):
        """The rule of the scenarios: name fuzzy weight 2, e-mail normalized weight 3."""
        return self.dup_rule(
            'res.partner', [('name', 'fuzzy', 2), ('email', 'normalized', 3)], threshold, **vals)

    def run_check(self, rule, user=None):
        """Start a duplicate check of the rule and let the background runner finish it."""
        Check = self.env['data.quality.duplicate.check']
        if user:
            Check = Check.with_user(user)
        check = Check.create({'rule_id': rule.id})
        check.action_start()
        self.drain_job(check)
        rule.invalidate_recordset()
        return check

    def open_groups(self, rule):
        return self.env['data.quality.duplicate.group'].sudo().search(
            [('rule_id', '=', rule.id), ('state', '=', 'open')])

    def group_ids(self, group):
        """The ids of the records of a group."""
        return {line.res_id for line in group.sudo().line_ids}

    def group_of(self, rule, record):
        """The open group of ``rule`` holding ``record`` (empty when none)."""
        return self.open_groups(rule).filtered(lambda g: record.id in self.group_ids(g))

    def make_group(self, rule, records):
        """The open group a check of ``rule`` finds for ``records`` (they must look alike)."""
        self.run_check(rule)
        groups = self.open_groups(rule).filtered(
            lambda g: set(records.ids) <= self.group_ids(g))
        self.assertEqual(len(groups), 1, 'the check found no group for %s' % records.mapped('name'))
        return groups

    def link_bank(self, partner, number):
        """A linked document of a contact (the module has no sales orders): a bank account."""
        return self.env['res.partner.bank'].create({'acc_number': number, 'partner_id': partner.id})

    # ---- merge previews ----------------------------------------------------

    def open_preview(self, group=None, records=None, user=None):
        """The merge preview, opened as the manager does and saved (it is a temporary record).

        From a group the preview takes the group of the action's context; otherwise it takes the
        record ids named in the context. Which of the two the build reads is left to the build.
        """
        Wizard = self.env['data.quality.merge.wizard']
        if user:
            Wizard = Wizard.with_user(user)
        if group:
            context = {
                'active_model': 'data.quality.duplicate.group', 'active_id': group.id,
                'active_ids': group.ids, 'default_group_id': group.id,
            }
            if 'default_res_model' not in context:
                context['default_res_model'] = group.sudo().rule_id.model_name
            context['default_res_ids'] = sorted(self.group_ids(group))
        else:
            context = {
                'active_model': records._name, 'active_ids': records.ids,
                'default_res_model': records._name, 'default_res_ids': sorted(records.ids),
            }
        with Form(Wizard.with_context(**context)) as form:
            pass
        return form.record

    def confirm_merge(self, wizard):
        return call_first(wizard, CONFIRM)

    def merge_of(self, group):
        return self.env['data.quality.merge'].sudo().search([('group_id', '=', group.id)])

    # ---- corrections -------------------------------------------------------

    def correction(self, model_name, field_name, action_words, user=None, rule=None, domain_ids=None,
                   **vals):
        """A correction saved in draft by ``user`` (a manager unless told otherwise)."""
        M2BCase._seq += 1
        Correction = self.env['data.quality.correction']
        if user is not False:
            Correction = Correction.with_user(user or self.manager_a)
        values = {
            'name': 'DGQ correction %s' % M2BCase._seq,
            'model_id': self.model(model_name).id,
            'field_id': self.fld(model_name, field_name).id,
            'action': choice(self.env, 'data.quality.correction', 'action', action_words),
            'company_ids': [Command.set([self.cx.id])],
        }
        if rule:
            values['rule_id'] = rule.id
        if domain_ids is not None:
            values['domain'] = str([('id', 'in', list(domain_ids))])
        values.update(vals)
        return Correction.create(values)

    def dry_run(self, correction):
        call_first(correction, DRY_RUN)
        correction.invalidate_recordset()
        self.drain_job(correction.run_ids)
        correction.invalidate_recordset()
        return correction.last_dry_run_id

    def approve(self, correction, user):
        as_user = correction.with_user(user)
        call_first(as_user, APPROVE)
        correction.invalidate_recordset()

    def real_run(self, correction):
        before = correction.run_ids.ids
        call_first(correction, REAL_RUN)
        correction.invalidate_recordset()
        runs = correction.run_ids.filtered(lambda r: r.id not in before and r.kind == 'run')
        self.drain_job(runs)
        correction.invalidate_recordset()
        runs = correction.run_ids.filtered(lambda r: r.kind == 'run').sorted('id')
        self.assertTrue(runs, 'the real run left no run record')
        return runs[-1]

    def lines(self, run, state=None):
        found = run.sudo().log_ids
        return found.filtered(lambda line: line.state == state) if state else found

    def line_of(self, run, record, state=None):
        found = self.lines(run, state).filtered(lambda line: line.res_id == record.id)
        return found[:1]

    def issue_for(self, rule, partner):
        return self.env['data.quality.issue'].create({
            'rule_id': rule.id, 'res_model': 'res.partner', 'res_id': partner.id,
            'company_id': self.cx.id, 'severity': rule.severity, 'record_name': partner.name})

    def setUp(self):
        super().setUp()
        self.manager_a = self.user('manager', login='dgq_m2b_ann', company=self.cx)
        self.manager_b = self.user('manager', login='dgq_m2b_bob', company=self.cx)
        self.administrator = self.user('admin', login='dgq_m2b_root', company=self.cx)

    @staticmethod
    def matches(pattern, text):
        return re.search(pattern, text or '') is not None
