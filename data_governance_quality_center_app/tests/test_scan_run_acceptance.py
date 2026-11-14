from unittest.mock import patch

from .acceptance_helpers import AcceptanceCase

BATCH = 1000


def batch_sizes(total):
    return [BATCH] * (total // BATCH) + ([total % BATCH] if total % BATCH else [])


class TestScanRunAcceptance(AcceptanceCase):
    """Acceptance tests of T-010: batches, checkpoints, resume and the recorded results of a scan.

    The batch contract is the job mixin's: ``_dgq_run_batch`` returns ``(processed, remaining)``.
    The tests watch it through a spy around the real method and read the checkpoint and the
    result lines the batch has saved. SC-DGQ-0016-2 (a save is not held up) is a statement about
    locking that no test can show without a second database session; NFR-DGQ-0001 is measured by
    the benchmark delivered with the app, not by a test of this suite.

    Requirements: FR-DGQ-0016, FR-DGQ-0017, FR-DGQ-0019, NFR-DGQ-0001
    """

    def _spy(self, fail_on_batch=None):
        """Wrap the scan's batch method: record each batch's answer and what it has saved."""
        Scan = type(self.env['data.quality.scan'])
        original = Scan._dgq_run_batch
        log = []
        stopped = []

        def spy(this):
            # The stop fires once: the failed batch is not logged (it saved nothing), so the
            # resume's first call sees the same count and must not stop again.
            if fail_on_batch and not stopped and len(log) + 1 == fail_on_batch:
                stopped.append(True)
                raise RuntimeError('simulated stop')
            result = original(this)
            checkpoints = this.env['data.quality.scan.checkpoint'].search([('scan_id', '=', this.id)])
            log.append({
                'result': result,
                'records_done': sum(checkpoints.mapped('records_done')),
                'last_id': max(checkpoints.mapped('last_id') or [0]),
                'lines': this.env['data.quality.scan.line'].search_count([('scan_id', '=', this.id)]),
            })
            return result

        patcher = patch.object(Scan, '_dgq_run_batch', spy)
        patcher.start()
        self.addCleanup(patcher.stop)
        return log

    def _failing_partners(self, count):
        """``count`` partners of company X with no reference, and the rule that fails them."""
        rule = self.make_rule('res.partner', 'required', ['ref'])
        self.env['res.partner'].create([
            {'name': 'Bulk %s' % n, 'company_id': self.cx.id} for n in range(count)])
        total = self.env['res.partner'].search_count([('company_id', '=', self.cx.id)])
        ids = sorted(self.env['res.partner'].search([('company_id', '=', self.cx.id)]).ids)
        return rule, total, ids

    # ---- FR-DGQ-0016 -------------------------------------------------------

    def test_records_are_checked_in_batches_of_1000_with_a_checkpoint(self):
        """SC-DGQ-0016-1: 2,500 records make batches of 1,000, 1,000 and 500, each saved with its checkpoint."""
        rule, total, ids = self._failing_partners(2500)
        log = self._spy()
        scan = self.run_scan([rule], models=['res.partner'])
        self.assertEqual(scan.state, 'done')
        sizes = batch_sizes(total)
        self.assertEqual([entry['result'][0] for entry in log], sizes)
        self.assertEqual([entry['result'][1] for entry in log], [total - sum(sizes[:i + 1]) for i in range(len(sizes))])
        done = [sum(sizes[:i + 1]) for i in range(len(sizes))]
        self.assertEqual([entry['records_done'] for entry in log], done)
        self.assertEqual([entry['lines'] for entry in log], done)  # every record fails: results saved per batch
        self.assertEqual([entry['last_id'] for entry in log], [ids[count - 1] for count in done])

    # ---- FR-DGQ-0017 -------------------------------------------------------

    def test_failed_scan_resumes_after_its_checkpoint(self):
        """SC-DGQ-0017-1: after 3 saved batches of 5,000 records, the resume checks the rest only."""
        rule, total, ids = self._failing_partners(5000)
        log = self._spy(fail_on_batch=4)
        scan = self.make_scan([rule], models=['res.partner'])
        scan.action_start()
        self.drain(scan)
        self.assertEqual(scan.sudo().state, 'failed')
        self.assertTrue(scan.sudo().error_message)
        self.assertEqual([entry['records_done'] for entry in log], [1000, 2000, 3000])
        scan.action_resume()
        self.assertEqual(scan.state, 'queued')
        self.drain(scan)
        self.assertEqual(scan.sudo().state, 'done')
        resumed = [entry['result'][0] for entry in log[3:]]
        self.assertEqual(resumed, batch_sizes(total - 3000))
        self.assertEqual(log[3]['last_id'], ids[3999])  # the first resumed batch starts at record 3,001
        self.assertEqual(scan.sudo().records_scanned, total)
        lines = scan.sudo().line_ids
        self.assertEqual(len(lines), total)
        self.assertEqual(len(set(lines.mapped('res_id'))), total)  # no record counted twice

    def test_failed_scan_shows_its_error_and_a_resume_button(self):
        """SC-DGQ-0017-2: a scan failed on a database error shows state failed, the error and Resume."""
        rule = self.make_rule('res.partner', 'required', ['ref'])
        self.partner('Acme')
        self._spy(fail_on_batch=1)
        scan = self.make_scan([rule], models=['res.partner'])
        scan.action_start()
        self.drain(scan)
        self.assertEqual(scan.sudo().state, 'failed')
        self.assertTrue(scan.sudo().error_message)
        arch = self.env['data.quality.scan'].get_view(view_type='form')['arch']
        self.assertIn('action_resume', arch)

    # ---- FR-DGQ-0019 -------------------------------------------------------

    def test_finished_scan_records_counts_and_one_line_per_failed_check(self):
        """SC-DGQ-0019-1: 10 contacts, a critical rule failing 2 and a warning rule failing 3 others."""
        critical = self.make_rule('res.partner', 'required', ['ref'], severity='critical')
        warning = self.make_rule('res.partner', 'required', ['function'], severity='warning')
        before = self.run_scan([critical, warning], models=['res.partner'])
        base = (before.records_scanned, before.records_error, before.records_warning,
                before.checks_passed, before.checks_failed, before.checks_not_applicable)
        mine = []
        for n in range(10):
            vals = {'ref': 'R-%s' % n, 'function': 'Job'}
            if n < 2:
                del vals['ref']
            elif n < 5:
                del vals['function']
            mine.append(self.partner('Contact %s' % n, **vals))
        scan = self.run_scan([critical, warning], models=['res.partner'])
        now = (scan.records_scanned, scan.records_error, scan.records_warning,
               scan.checks_passed, scan.checks_failed, scan.checks_not_applicable)
        self.assertEqual(tuple(a - b for a, b in zip(now, base)), (10, 2, 3, 15, 5, 0))
        ids = {partner.id for partner in mine}
        lines = [line for line in scan.sudo().line_ids if line.res_id in ids]
        self.assertEqual(len(lines), 5)
        self.assertEqual(sorted((line.rule_id.id, line.severity) for line in lines),
                         sorted([(critical.id, 'critical')] * 2 + [(warning.id, 'warning')] * 3))
        for line in lines:
            self.assertEqual(line.rule_version, 1)
            self.assertTrue(self.text(line))
            self.assertEqual(line.scan_id, scan.sudo())

    def test_scan_without_a_failure_has_no_line(self):
        """SC-DGQ-0019-2: when no record fails, the scan shows its records scanned, 0 failed, no line."""
        rule = self.make_rule('res.partner', 'required', ['name'])
        for n in range(3):
            self.partner('Fine %s' % n)
        total = self.env['res.partner'].search_count([('company_id', '=', self.cx.id)])
        scan = self.run_scan([rule], models=['res.partner'])
        self.assertEqual(scan.state, 'done')
        self.assertEqual(scan.records_scanned, total)
        self.assertEqual(scan.checks_failed, 0)
        self.assertEqual(scan.checks_passed, total)
        self.assertFalse(scan.sudo().line_ids)

    def test_running_counts_survive_in_tallies(self):
        """FR-DGQ-0019 (design): each batch adds its counts per model, company and rule to a tally."""
        rule, total, _ids = self._failing_partners(30)
        scan = self.run_scan([rule], models=['res.partner'])
        tallies = scan.sudo().tally_ids.filtered(lambda t: t.company_id == self.cx)
        model_row = tallies.filtered(lambda t: not t.rule_id)
        rule_row = tallies.filtered(lambda t: t.rule_id == rule)
        self.assertEqual(model_row.records_scanned, total)
        self.assertEqual(rule_row.records_applicable, total)
