from unittest.mock import patch

from psycopg2 import IntegrityError

from odoo.tools import mute_logger

from .acceptance_helpers import AcceptanceCase


class TestScanRun(AcceptanceCase):
    """Own tests of the scan batches: what a batch reads and how it tallies.

    Requirements: FR-DGQ-0016, FR-DGQ-0019
    """

    def test_company_limited_rules_leave_records_without_company_unread(self):
        """FR-DGQ-0016: a rule of one company never reads, nor counts, the records of no company."""
        rule = self.make_rule('res.partner', 'required', ['ref'])
        self.env['res.partner'].create({'name': 'Nobody', 'company_id': False})
        self.partner('Mine')
        scan = self.run_scan([rule], models=['res.partner'])
        total = self.env['res.partner'].search_count([('company_id', '=', self.cx.id)])
        self.assertEqual(scan.records_scanned, total)
        self.assertEqual(scan.sudo().checkpoint_ids.records_done, total)

    def test_company_free_rule_reads_records_without_company(self):
        """FR-DGQ-0016: a rule with no company reads the records of no company too."""
        rule = self.make_rule('res.partner', 'required', ['ref'], company=False)
        orphan = self.env['res.partner'].create({'name': 'Nobody', 'company_id': False})
        scan = self.run_scan([rule], models=['res.partner'])
        self.assertIn(orphan.id, scan.sudo().line_ids.mapped('res_id'))

    def test_record_failing_two_rules_counts_once_per_model(self):
        """FR-DGQ-0019: a record failing a critical and a warning rule is one failing record, with both marks."""
        critical = self.make_rule('res.partner', 'required', ['ref'], severity='critical')
        warning = self.make_rule('res.partner', 'required', ['function'], severity='warning')
        self.partner('Both')
        scan = self.run_scan([critical, warning], models=['res.partner'])
        row = scan.sudo().tally_ids.filtered(lambda t: t.company_id == self.cx and not t.rule_id)
        self.assertEqual((row.records_failing, row.records_error, row.records_warning), (1, 1, 1))
        self.assertEqual((scan.records_error, scan.records_warning), (1, 1))
        self.assertEqual(scan.checks_failed, 2)

    def test_tally_keeps_the_rule_version_over_several_batches(self):
        """FR-DGQ-0016: only counters add up from batch to batch; the rule version stays the rule's."""
        rule = self.make_rule('res.partner', 'required', ['ref'])
        rule.severity = 'critical'  # a definition change: the rule is now at version 2
        for index in range(5):
            self.partner('Batch %s' % index)
        with patch('odoo.addons.data_governance_quality_center_app.models.scan.JOB_BATCH_SIZE', 2):
            scan = self.run_scan([rule], models=['res.partner'])
        total = self.env['res.partner'].search_count([('company_id', '=', self.cx.id)])
        row = scan.sudo().tally_ids.filtered(lambda t: t.rule_id == rule and t.company_id == self.cx)
        self.assertEqual((row.rule_version, row.records_applicable), (2, total))

    def test_tally_model_row_is_unique(self):
        """FR-DGQ-0019: a scan has one model row per model and company, though the rule is empty."""
        self.partner('Counted')
        scan = self.run_scan([self.make_rule('res.partner', 'required', ['ref'])], models=['res.partner'])
        row = scan.sudo().tally_ids.filtered(lambda t: not t.rule_id)[:1]
        self.assertTrue(row)
        with self.assertRaises(IntegrityError), mute_logger('odoo.sql_db'), self.cr.savepoint():
            row.copy()

    def test_scan_counts_issues_from_zero(self):
        """FR-DGQ-0019: the scan holds its issue counts, filled by the issue task."""
        scan = self.run_scan([self.make_rule('res.partner', 'required', ['ref'])], models=['res.partner'])
        self.assertEqual((scan.issues_created, scan.issues_updated, scan.issues_resolved), (0, 0, 0))
