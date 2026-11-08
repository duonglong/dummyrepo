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
