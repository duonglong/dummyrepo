from unittest.mock import patch

from odoo import Command
from odoo.tests import Form

from .acceptance_m2b_helpers import CONFIRM, ROLLBACK, call_first
from .acceptance_m3_helpers import M3Case


class TestAuditEvents(M3Case):
    """Every place that changes a rule, an issue, a scan, a merge or a correction writes its audit entry.

    One row per place: the rule's create, write and unlink; an issue changed by hand, by the bulk
    dialog and by a scan; a scan's start and end (also of an automation); a merge and its
    rollback; a correction's values and the rollback of them (also of an automation).
    The screens are not driven: the audit list is read through the model as an administrator does.

    Requirements: FR-DGQ-0072, BR-DGQ-0010
    """

    def setUp(self):
        super().setUp()
        self.rule = self.make_rule('product.template', 'range', ['x_dgq_cost'], has_max=True, max_value=10000)

    def entries(self, event, record=None):
        domain = [('event', '=', event)]
        if record:
            domain += [('res_model', '=', record._name), ('res_id', '=', record.id)]
        return self.env['data.quality.audit'].sudo().search(domain, order='id')

    def issue(self, name='Audited', rule=None):
        rule = rule or self.rule
        product = self.product(name)
        return self.env['data.quality.issue'].create({
            'rule_id': rule.id, 'res_model': 'product.template', 'res_id': product.id,
            'company_id': self.cx.id, 'severity': 'warning', 'record_name': name})

    # ---- rules ---------------------------------------------------------------

    def test_a_rule_that_is_created_is_audited(self):
        """SC-DGQ-0072-1 (creation): an entry names the rule, the user and the time."""
        entry = self.entries('rule_created', self.rule)
        self.assertEqual(len(entry), 1)
        self.assertEqual(entry.record_name, self.rule.name)
        self.assertEqual(entry.user_id, self.env.user)

    def test_a_rule_changed_by_a_manager_records_the_values_before_and_after(self):
        """SC-DGQ-0072-1: Ann changes the maximum 10000 to 20000; the entry shows rule changed, Ann and the values."""
        self.rule.with_user(self.manager_a).write({'max_value': 20000})
        entry = self.entries('rule_changed', self.rule)
        self.assertEqual(len(entry), 1)
        self.assertEqual(entry.user_id, self.manager_a)
        self.assertEqual(entry.changes, {'max_value': [10000.0, 20000.0]})
        self.assertEqual(entry.field_names, 'max_value')

    def test_a_rule_switched_off_or_renamed_is_audited_but_a_same_value_is_not(self):
        """FR-DGQ-0072: the name and the switch are changes too; writing the value it has is none."""
        self.rule.write({'max_value': 10000})
        self.assertFalse(self.entries('rule_changed', self.rule))
        old_name = self.rule.name
        self.rule.write({'active': False, 'name': 'Renamed'})
        self.assertEqual(
            self.entries('rule_changed', self.rule).changes,
            {'active': [True, False], 'name': [old_name, 'Renamed']})

    def test_a_rule_that_is_deleted_is_audited(self):
        """FR-DGQ-0072: the deletion leaves an entry that still names the rule."""
        name, rule_id = self.rule.name, self.rule.id
        self.rule.unlink()
        entry = self.env['data.quality.audit'].sudo().search([('event', '=', 'rule_deleted'), ('res_id', '=', rule_id)])
        self.assertEqual(entry.record_name, name)

    # ---- issues --------------------------------------------------------------

    def test_an_issue_assigned_resolved_ignored_and_reopened_by_hand_is_audited(self):
        """FR-DGQ-0072: each change of an issue by write leaves its own entry with the status or person before and after."""
        ann = self.user('operator', login='dgq_audit_ann', company=self.cx)
        issue = self.issue()
        issue.write({'user_id': ann.id})
        issue.write({'state': 'resolved'})
        issue.write({'state': 'new'})
        issue.write({'state': 'ignored', 'ignore_reason': 'accepted'})
        self.assertEqual(self.entries('issue_assigned', issue).changes, {'user_id': [False, ann.name]})
        self.assertEqual(self.entries('issue_resolved', issue).changes, {'state': ['assigned', 'resolved']})
        self.assertEqual(len(self.entries('issue_reopened', issue)), 1)
        self.assertEqual(self.entries('issue_ignored', issue).changes, {'state': ['new', 'ignored']})
        issue.write({'state': 'new'})
        self.assertEqual(len(self.entries('issue_reopened', issue)), 2)

    def test_an_issue_changed_by_the_bulk_dialog_is_audited(self):
        """FR-DGQ-0072: the bulk dialog writes each issue, so each one leaves its entry."""
        issues = self.issue('Bulk one') | self.issue('Bulk two')
        operator = self.user('operator', login='dgq_audit_bulk', company=self.cx)
        Bulk = self.env['data.quality.issue.bulk'].with_user(operator)
        with Form(Bulk.with_context(default_issue_ids=issues.ids)) as form:
            form.action = 'resolve'
        form.record.action_apply()
        for issue in issues:
            entry = self.entries('issue_resolved', issue)
            self.assertEqual((len(entry), entry.user_id), (1, operator))

    def test_an_issue_resolved_and_reopened_by_a_scan_is_audited(self):
        """FR-DGQ-0072: a scan that closes an issue and one that finds it again leave entries."""
        product = self.product('Scanned')
        product.x_dgq_cost = 20000
        scan = self.run_scan([self.rule], models=['product.template'])
        issue = self.env['data.quality.issue'].search([('rule_id', '=', self.rule.id), ('res_id', '=', product.id)])
        self.assertTrue(issue)
        product.x_dgq_cost = 5
        self.run_scan([self.rule], models=['product.template'])
        self.assertEqual(len(self.entries('issue_resolved', issue)), 1)
        product.x_dgq_cost = 20000
        self.run_scan([self.rule], models=['product.template'])
        self.assertEqual(len(self.entries('issue_reopened', issue)), 1)
        self.assertTrue(scan)

    # ---- scans ---------------------------------------------------------------

    def test_a_scan_that_starts_and_ends_is_audited_with_its_requester(self):
        """FR-DGQ-0072: a scan leaves a started and an ended entry for the user who requested it."""
        self.product('Fine')
        scan = self.run_scan([self.rule], models=['product.template'], user=self.operator)
        started, ended = self.entries('scan_started', scan), self.entries('scan_ended', scan)
        self.assertEqual((len(started), len(ended)), (1, 1))
        self.assertEqual((started.user_id, ended.user_id), (self.operator, self.operator))
        self.assertEqual(ended.changes, {'state': ['running', 'done']})

    def test_a_scan_of_an_automation_is_audited_under_the_automation(self):
        """FR-DGQ-0072: the entries of a scan an automation runs name the automation."""
        rule = self.make_rule('res.partner', 'required', ['x_dgq_born_on'])
        self.partner('Born unknown')
        automation = self.make_automation('Nightly contacts', rules=[rule])
        self.make_due(automation)
        self.advance(3)
        scan = automation.sudo().run_ids.scan_id
        self.assertTrue(scan)
        for event in ('scan_started', 'scan_ended'):
            self.assertEqual(self.entries(event, scan).automation_id, automation)

    # ---- merges --------------------------------------------------------------

    def merged(self):
        rule = self.contact_rule()
        acme = self.partner('Acme Ltd', email='info@acme.com')
        acme_two = self.partner('ACME Ltd.', email='INFO@acme.com', phone='555-0100')
        group = self.make_group(rule, acme | acme_two)
        Wizard = self.env['data.quality.merge.wizard'].with_user(self.manager_a)
        context = {
            'default_group_id': group.id, 'default_res_model': 'res.partner',
            'default_res_ids': sorted(self.group_ids(group))}
        with Form(Wizard.with_context(**context)) as form:
            form.kept_res_id = acme.id
        call_first(form.record, CONFIRM)
        self.env.flush_all()
        return acme, self.merge_of(group)

    def test_a_merge_and_its_rollback_are_audited(self):
        """FR-DGQ-0072: the merge names the record kept and the value written; the rollback names it too."""
        kept, merge = self.merged()
        done = self.entries('merge_done', kept)
        self.assertEqual((len(done), done.user_id), (1, self.manager_a))
        self.assertEqual(done.changes, {'phone': [False, '555-0100']})
        call_first(merge.with_user(self.manager_a), ROLLBACK)
        rolled_back = self.entries('merge_rolled_back', kept)
        self.assertEqual((len(rolled_back), rolled_back.user_id), (1, self.manager_a))

    # ---- corrections ---------------------------------------------------------

    def trimmed(self, **kwargs):
        partner = self.partner('Acme', email=' A@B.COM ')
        correction = self.correction('res.partner', 'email', ['trim'], domain_ids=partner.ids, **kwargs)
        return partner, correction

    def test_a_correction_run_names_the_contact_with_the_values_before_and_after(self):
        """SC-DGQ-0072-2: ' A@B.COM ' to 'A@B.COM' appears under the contact, not only under the correction."""
        partner, correction = self.trimmed()
        self.dry_run(correction)
        self.assertFalse(self.entries('correction_run', partner))
        self.real_run(correction)
        entry = self.entries('correction_run', partner)
        self.assertEqual(len(entry), 1)
        self.assertEqual(entry.changes, {'email': [' A@B.COM ', 'A@B.COM']})
        self.assertEqual(entry.user_id, self.manager_a)

    def test_the_values_of_a_sensitive_field_are_not_kept_in_the_audit_trail(self):
        """BR-DGQ-0010: a sensitive field is recorded as changed without its values."""
        partner, correction = self.trimmed()
        with patch.object(type(correction), '_dgq_is_sensitive', lambda self: True):
            self.dry_run(correction)
            self.approve(correction, self.manager_b)
            self.real_run(correction)
        entry = self.entries('correction_run', partner)
        self.assertEqual((len(entry), entry.field_names, entry.changes), (1, 'email', False))

    def test_the_rollback_of_a_correction_names_the_contact_with_the_values_put_back(self):
        """FR-DGQ-0072: each value restored by a rollback appears under its record."""
        partner, correction = self.trimmed()
        self.dry_run(correction)
        run = self.real_run(correction)
        call_first(run.with_user(self.manager_a), ROLLBACK)
        self.drain_job(self.env['data.quality.correction.run'].sudo().search([('rollback_of_id', '=', run.id)]))
        entry = self.entries('correction_rolled_back', partner)
        self.assertEqual(len(entry), 1)
        self.assertEqual(entry.changes, {'email': ['A@B.COM', ' A@B.COM ']})

    def test_a_correction_run_by_an_automation_is_audited_under_the_automation(self):
        """FR-DGQ-0072: the values an automation's correction changes and the run entry name the automation."""
        rule = self.make_rule('res.partner', 'pattern', ['name'], pattern=r'^\S(.*\S)?$', name='Trimmed names')
        partner = self.env['res.partner'].create({'name': '  Padded  ', 'company_id': self.cx.id})
        correction = self.correction('res.partner', 'name', ['trim'], rule=rule, domain="[('id', '>', 0)]",
                                     company_ids=[Command.clear()])
        self.dry_run(correction)
        correction.with_user(self.manager_b).write({'auto_approved': True})
        automation = self.make_automation('Nightly trim', rules=[rule], correction_ids=[correction])
        self.make_due(automation)
        self.advance(3)
        self.assertEqual(partner.name, 'Padded')
        entries = self.entries('correction_run').filtered(
            lambda entry: entry.automation_id == automation)
        self.assertEqual(set(entries.mapped('res_model')), {'res.partner', 'data.quality.correction'})
