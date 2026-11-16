from odoo import fields
from odoo.exceptions import UserError

from .acceptance_m3_helpers import M3Case, csv_file

HEADER = ['Name', 'Email', 'Country']
BASE_FORGERY = {
    'state': 'done', 'user_id': 1, 'date_queued': '2020-01-01 00:00:00', 'date_start': '2020-01-01 00:00:00',
    'date_end': '2020-01-01 00:00:00', 'heartbeat': '2020-01-01 00:00:00', 'total_count': 5, 'done_count': 5,
    'error_message': 'forged', 'progress': 100.0,
}
SESSION_FORGERY = {
    'job_kind': 'import', 'error_count': 0, 'warning_count': 0, 'error_row_count': 0, 'row_count': 9,
    'date_checked': '2020-01-01 00:00:00', 'date_imported': '2020-01-01 00:00:00', 'import_state': 'imported',
    'imported_count': 9, 'next_row': 9, 'file_checksum': 'forged', 'column_mapping': {'Name': 'name'},
    'import_after_check': True, 'res_model': 'res.users',
}
PROFILE_FORGERY = {'record_count': 123456, 'stale_count': 7, 'next_field_index': 3, 'res_model': 'res.users'}
CHECK_FORGERY = {
    'phase': 'groups', 'last_id': 5, 'last_key': 'forged', 'pair_data': [[1, 2, 99]], 'pairs_scored': 9,
    'blocks_skipped': 9, 'groups_found': 999,
}


class TestJobGuard(M3Case):
    """Regression tests for B-0222: the state and results of a background job are written by the app only.

    Each row is one field of one job kind, written by a user through a plain create or write as RPC does. The
    screens cannot reach it: a readonly field is never sent by the form, so only the ORM calls are covered.

    Requirements: NFR-DGQ-0005, NFR-DGQ-0006, FR-DGQ-0060, FR-DGQ-0062, FR-DGQ-0070
    """

    def setUp(self):
        super().setUp()
        self.manager = self.user('manager', login='dgq_b0222_manager', company=self.cx)

    def bad_file(self):
        return csv_file(HEADER, [['Guard bad', 'guard@example.com', 'Atlantis']])

    def session(self):
        return self.make_session(self.bad_file(), 'bad.csv')

    def profile(self):
        return self.make_profile('res.partner', ['name'])

    def check(self):
        rule = self.contact_rule()
        return self.env['data.quality.duplicate.check'].with_user(self.manager).create({'rule_id': rule.id})

    def kinds(self):
        return (
            ('import session', self.session, {**BASE_FORGERY, **SESSION_FORGERY}),
            ('profile', self.profile, {**BASE_FORGERY, **PROFILE_FORGERY}),
            ('duplicate check', self.check, {**BASE_FORGERY, **CHECK_FORGERY}),
        )

    def test_bug_b_0222_a_user_cannot_write_a_job_field_by_hand(self):
        """B-0222: every state, progress and result field of each job kind is refused on a plain write."""
        for kind, make, forgery in self.kinds():
            job = make()
            for name, value in forgery.items():
                with self.subTest(kind=kind, field=name), self.assertRaisesRegex(UserError, "set by the app"):
                    job.write({name: value})

    def test_bug_b_0222_a_user_cannot_create_a_job_with_a_job_field(self):
        """B-0222: the same fields are refused on create, so a job cannot be born done.

        A zero count is what a new job holds anyway, so only the other values are tried.
        """
        for kind, make, forgery in self.kinds():
            job = make()
            vals = {'rule_id': job.rule_id.id} if kind == 'duplicate check' else {
                'model_id': job.model_id.id, 'company_id': self.cx.id}
            forged = {name: value for name, value in forgery.items() if value != 0}
            for name, value in forged.items():
                with self.subTest(kind=kind, field=name), self.assertRaisesRegex(UserError, "set by the app"):
                    job.browse().create({**vals, name: value})

    def test_bug_b_0222_a_context_default_does_not_set_a_job_field(self):
        """B-0222: a default passed in the context cannot give a new job a state."""
        for kind, make, _forgery in self.kinds():
            job = make()
            created = job.with_context(default_state='done', default_done_count=9).copy()
            with self.subTest(kind=kind):
                self.assertEqual((created.state, created.done_count), ('draft', 0))

    def test_bug_b_0222_forged_error_count_does_not_open_the_strict_import(self):
        """B-0222, FR-DGQ-0060: a strict check with an error row is still refused after the count is forged."""
        session = self.check_file(self.session())
        self.assertEqual(session.error_row_count, 1)
        with self.assertRaises(UserError):
            session.write({'error_row_count': 0})
        with self.assertRaisesRegex(UserError, "rows have errors"):
            session.action_import()
        self.assertEqual(session.import_state, 'not_imported')

    def test_bug_b_0222_the_buttons_still_run_the_job_of_a_user(self):
        """B-0222: Start, Cancel and Resume write the job fields for the user who may write the job."""
        for kind, make, _forgery in self.kinds():
            job = make()
            with self.subTest(kind=kind):
                job.action_start()
                self.assertEqual((job.state, job.user_id), ('queued', job.env.user))
                job.action_cancel()
                self.assertEqual(job.state, 'cancelled')
                job.action_start()
                self.assertEqual(job.state, 'queued')
                self.assertTrue(fields.Datetime.to_string(job.date_queued))
