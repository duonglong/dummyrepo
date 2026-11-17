from odoo import Command
from odoo.exceptions import UserError

from .acceptance_m3_helpers import M3Case, csv_file

HEADER = ['Name', 'Email', 'Country']
REFUSED = "can only be changed"


class TestJobScopeGuard(M3Case):
    """Regression tests for B-0226: the scope of a job is fixed once the job has been started.

    Each row is one scope field of one job kind, written by a user through a plain write as RPC does,
    after the job ran or while it waits. The forms show these fields read-only, so only the ORM call
    is covered, not what a screen offers.

    Requirements: FR-DGQ-0039, FR-DGQ-0060, FR-DGQ-0070, FR-DGQ-0071, BR-DGQ-0011
    """

    def setUp(self):
        super().setUp()
        self.operator.write({'company_ids': [Command.link(self.cy.id)]})
        self.manager = self.user('manager', login='dgq_b0226_manager', company=self.cx)

    def done_session(self):
        content = csv_file(HEADER, [['Scope probe', 'scope@example.com', 'Atlantis']])
        return self.check_file(self.make_session(content, 'scope.csv'))

    def done_profile(self):
        return self.run_profile(self.make_profile('res.partner', ['email']))

    def queued_check(self):
        check = self.env['data.quality.duplicate.check'].with_user(self.manager).create({
            'rule_id': self.contact_rule().id})
        check.action_start()
        return check

    def other_rule(self):
        return self.dup_rule('res.partner', [('name', 'exact', 1)], 85, name='Other scope rule')

    def test_bug_b_0226_import_check_scope_is_fixed_after_the_check(self):
        """B-0226: model, company, rules and duplicate fields of a checked file cannot be written."""
        session = self.done_session()
        rule = self.vendor_rule()
        changes = {
            'company_id': self.cy.id,
            'model_id': self.model('product.template').id,
            'rule_ids': [Command.set(rule.ids)],
            'duplicate_field_ids': [Command.set(self.fld('res.partner', 'email').ids)],
        }
        for name, value in changes.items():
            with self.subTest(field=name), self.assertRaisesRegex(UserError, REFUSED):
                session.write({name: value})

    def test_bug_b_0226_import_check_scope_is_fixed_while_it_is_queued(self):
        """B-0226: a queued check keeps the company it will be checked in."""
        session = self.make_session(csv_file(HEADER, [['Queued', 'q@example.com', 'Atlantis']]), 'q.csv')
        session.action_start()
        with self.assertRaisesRegex(UserError, REFUSED):
            session.write({'company_id': self.cy.id})

    def test_bug_b_0226_import_scope_stays_open_where_the_start_reads_it_again(self):
        """B-0226: a draft, and a cancelled check with nothing imported, can still be given another scope."""
        session = self.make_session(csv_file(HEADER, [['Draft', 'd@example.com', 'Atlantis']]), 'd.csv')
        session.write({'company_id': self.cy.id})
        self.assertEqual(session.company_id, self.cy)
        session.action_start()
        session.action_cancel()
        session.write({'company_id': self.cx.id})
        self.assertEqual(session.company_id, self.cx)

    def test_bug_b_0226_import_scope_is_fixed_after_rows_were_imported(self):
        """B-0226: a failed import that already imported rows keeps its scope."""
        session = self.done_session()
        session.sudo().write({'state': 'failed', 'imported_count': 1})
        with self.assertRaisesRegex(UserError, REFUSED):
            session.write({'company_id': self.cy.id})

    def test_bug_b_0226_profile_scope_is_fixed_after_the_run(self):
        """B-0226: model, company, fields and stale limit of a finished profile cannot be written."""
        profile = self.done_profile()
        changes = {
            'company_id': self.cy.id,
            'model_id': self.model('product.template').id,
            'field_ids': [Command.set(self.fld('res.partner', 'name').ids)],
            'stale_days': 5,
        }
        for name, value in changes.items():
            with self.subTest(field=name), self.assertRaisesRegex(UserError, REFUSED):
                profile.write({name: value})

    def test_bug_b_0226_profile_scope_is_fixed_while_it_is_queued(self):
        """B-0226: a queued profile keeps its company."""
        profile = self.make_profile('res.partner', ['email'])
        profile.action_start()
        with self.assertRaisesRegex(UserError, REFUSED):
            profile.write({'company_id': self.cy.id})

    def test_bug_b_0226_a_draft_profile_can_be_changed_and_a_cancelled_one_started(self):
        """B-0226: the scope is open in draft; a profile cancelled with no fields still gets its default list."""
        profile = self.make_profile('res.partner', [])
        profile.write({'stale_days': 5})
        self.assertEqual(profile.stale_days, 5)
        profile.action_cancel()
        profile.action_start()
        self.assertEqual(profile.state, 'queued')
        self.assertTrue(profile.field_ids)

    def test_bug_b_0226_duplicate_check_rule_is_fixed_once_queued(self):
        """B-0226: a queued check cannot be pointed at a rule that has a queued check already."""
        first = self.queued_check()
        second = self.env['data.quality.duplicate.check'].with_user(self.manager).create({
            'rule_id': self.other_rule().id})
        second.action_start()
        with self.assertRaisesRegex(UserError, REFUSED):
            second.write({'rule_id': first.rule_id.id})
        self.assertEqual(
            self.env['data.quality.duplicate.check'].sudo().search_count(
                [('rule_id', '=', first.rule_id.id), ('state', '=', 'queued')]), 1)

    def test_bug_b_0226_duplicate_check_rule_is_fixed_after_the_run_and_open_in_draft(self):
        """B-0226: a finished check keeps its rule; a draft can still be given another."""
        rule = self.contact_rule()
        done = self.run_check(rule, user=self.manager)
        with self.assertRaisesRegex(UserError, REFUSED):
            done.write({'rule_id': self.other_rule().id})
        draft = self.env['data.quality.duplicate.check'].with_user(self.manager).create({'rule_id': rule.id})
        other = self.other_rule()
        draft.write({'rule_id': other.id})
        self.assertEqual(draft.rule_id, other)

    def test_bug_b_0227_failed_import_check_scope_is_fixed_so_resume_cannot_skip_the_check(self):
        """B-0227: a failed check keeps company, model, rules and duplicate fields; Resume continues it as it is.

        It cannot reach the screen: the forms show these fields read-only, so the plain write is what is covered.
        """
        session = self.make_session(csv_file(HEADER, [['Failed', 'f@example.com', 'Atlantis']]), 'f.csv')
        session.action_start()
        session.sudo().write({'state': 'failed', 'next_row': 1, 'row_count': 1})
        changes = {
            'company_id': self.cy.id,
            'model_id': self.model('product.template').id,
            'rule_ids': [Command.set(self.vendor_rule().ids)],
            'duplicate_field_ids': [Command.set(self.fld('res.partner', 'email').ids)],
        }
        for name, value in changes.items():
            with self.subTest(field=name), self.assertRaisesRegex(UserError, REFUSED):
                session.write({name: value})

    def test_bug_b_0227_failed_import_with_nothing_imported_keeps_its_scope(self):
        """B-0227: a failed import that imported no row yet is resumed from its next row, so its company is fixed.

        It cannot reach the screen: the forms show these fields read-only, so the plain write is what is covered.
        """
        session = self.done_session()
        session.sudo().write({'state': 'failed', 'job_kind': 'import', 'imported_count': 0})
        with self.assertRaisesRegex(UserError, REFUSED):
            session.write({'company_id': self.cy.id})
