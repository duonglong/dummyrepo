from lxml import etree

from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestStoredValuesAreShown(TransactionCase):
    """Requirements: FR-OPA-0029, FR-OPA-0010, FR-OPA-0031, FR-OPA-0030 (B-0120).

    Values the spec asks to show are in the arch of the view that is meant to show them.
    """

    def _field_names(self, model, xmlid, xpath='//field'):
        view = self.env.ref(f'perf_audit.{xmlid}')
        arch = etree.fromstring(self.env[model].get_view(view.id)['arch'])
        return {node.get('name') for node in arch.xpath(xpath)}

    def test_bug_b_0120_finding_form_shows_the_apply_outcome(self):
        """FR-OPA-0029: the finding form shows who applied the fix, when, and the error."""
        names = self._field_names('perf.audit.finding', 'perf_audit_finding_view_form')
        self.assertLessEqual({'apply_state', 'applied_by', 'applied_date', 'apply_error'}, names)

    def test_bug_b_0120_check_results_show_the_skipped_part_and_thresholds(self):
        """FR-OPA-0010, FR-OPA-0031: the check results of a run show the skipped part and thresholds used."""
        names = self._field_names(
            'perf.audit.run', 'perf_audit_run_view_form', "//field[@name='check_result_ids']//list/field")
        self.assertLessEqual({'part_skip_reason', 'thresholds_used'}, names)

    def test_bug_b_0120_run_form_shows_the_thresholds_used(self):
        """FR-OPA-0031: the run form shows the global thresholds the run used."""
        names = self._field_names('perf.audit.run', 'perf_audit_run_view_form', "//sheet//group/group/field")
        self.assertIn('thresholds_used', names)

    def test_bug_b_0120_finding_list_shows_the_comparison_state(self):
        """FR-OPA-0030: the stand-alone findings list tells fixed rows from open ones."""
        names = self._field_names('perf.audit.finding', 'perf_audit_finding_view_list')
        self.assertIn('comparison_state', names)


@tagged('post_install', '-at_install')
class TestOpeningTheAppStartsNoRun(TransactionCase):
    """Requirements: FR-OPA-0001, FR-OPA-0033 (B-0122).

    Opening the app icon shows existing runs; a run starts only from an explicit button.
    """

    def test_bug_b_0122_app_icon_opens_the_runs_list(self):
        """FR-OPA-0001: the first menu under the app root is the runs list, not the start action."""
        root = self.env.ref('perf_audit.menu_perf_audit_root')
        first = root.child_id.sorted(lambda menu: (menu.sequence, menu.id))[:1]
        self.assertEqual(first.action._name, 'ir.actions.act_window')
        self.assertEqual(first.action.res_model, 'perf.audit.run')

    def test_bug_b_0122_runs_list_has_a_start_button(self):
        """FR-OPA-0001: the runs list offers a Start audit button that calls action_start_audit."""
        view = self.env.ref('perf_audit.perf_audit_run_view_list')
        arch = etree.fromstring(self.env['perf.audit.run'].get_view(view.id)['arch'])
        self.assertTrue(arch.xpath("//header/button[@name='action_start_audit'][@type='object']"))


@tagged('post_install', '-at_install')
class TestRunningRunForm(TransactionCase):
    """Requirements: FR-OPA-0001 (B-0126)."""

    def _arch(self):
        view = self.env.ref('perf_audit.perf_audit_run_view_form')
        return etree.fromstring(self.env['perf.audit.run'].get_view(view.id)['arch'])

    def test_bug_b_0126_running_run_form_says_so_and_offers_a_refresh(self):
        """FR-OPA-0001: a run form opened before the run ends tells so and has a Refresh button."""
        arch = self._arch()
        self.assertTrue(arch.xpath("//div[contains(@class, 'alert')][contains(@invisible, 'running')]"))
        self.assertTrue(arch.xpath("//header/button[@name='action_refresh'][@type='object']"))

    def test_bug_b_0126_refresh_changes_nothing_on_the_run(self):
        """FR-OPA-0001: Refresh only makes the client reload the record."""
        run = self.env['perf.audit.run'].create({'state': 'running'})
        self.assertFalse(run.action_refresh())
        self.assertEqual(run.state, 'running')


@tagged('post_install', '-at_install')
class TestRunsListAndFindingsTab(TransactionCase):
    """Requirements: FR-OPA-0004, FR-OPA-0030 (B-0128, B-0129).

    The runs list tells weak-evidence runs and same-minute runs apart; the findings opened from a run
    are grouped by check and counted like the header.
    """

    def _arch(self, xmlid):
        view = self.env.ref(f'perf_audit.{xmlid}')
        return etree.fromstring(self.env['perf.audit.run'].get_view(view.id)['arch'])

    def test_bug_b_0128_runs_list_shows_the_weak_evidence_mark(self):
        """FR-OPA-0004: the runs list has a weak-evidence column and marks those rows."""
        arch = self._arch('perf_audit_run_view_list')
        self.assertTrue(arch.xpath("//field[@name='weak_evidence']"))
        self.assertTrue(arch.xpath("//list[contains(@decoration-warning, 'weak_evidence')]"))

    def test_bug_b_0128_runs_started_in_one_minute_have_different_names(self):
        """FR-OPA-0004: the run name carries the seconds, so runs of one minute can be told apart."""
        first, second = self.env['perf.audit.run'].create([
            {'start_date': '2026-11-08 02:00:05'}, {'start_date': '2026-11-08 02:00:41'}])
        self.assertNotEqual(first.name, second.name)

    def test_bug_b_0129_findings_of_a_run_open_grouped_by_check(self):
        """FR-OPA-0030: every stat button of the run form opens the findings grouped by check."""
        arch = self._arch('perf_audit_run_view_form')
        buttons = arch.xpath("//div[@name='button_box']/button")
        self.assertTrue(buttons)
        for button in buttons:
            self.assertIn('search_default_group_code', button.get('context'))

    def test_bug_b_0129_findings_count_button_leaves_out_fixed_rows_like_its_figure(self):
        """FR-OPA-0030: the Findings button opens the rows its count holds, the ones not marked fixed."""
        arch = self._arch('perf_audit_run_view_form')
        button = arch.xpath("//div[@name='button_box']/button[.//field[@name='finding_count']]")[0]
        self.assertIn('search_default_not_fixed', button.get('context'))
