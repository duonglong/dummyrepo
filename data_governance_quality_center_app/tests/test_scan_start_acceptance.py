from odoo.exceptions import AccessError, UserError

from .acceptance_helpers import AcceptanceCase, dump


class TestScanStartAcceptance(AcceptanceCase):
    """Acceptance tests of T-009: start a scan, queue it, refuse overlaps, skip excluded fields.

    The scan is created through the registry and started with the job mixin's ``action_start``;
    the start dialog (``wizard/scan_wizard.py``) is not named by the design and is left to the build.
    The skip of an overlapping automatic run (SC-DGQ-0018-3) needs the automation model of a
    later task.

    Requirements: FR-DGQ-0015, FR-DGQ-0018, FR-DGQ-0020
    """

    # ---- FR-DGQ-0015 -------------------------------------------------------

    def test_operator_starts_a_scan_that_is_queued_at_once(self):
        """SC-DGQ-0015-1: the operator's start answers with a queued scan; later it is done with counts."""
        operator = self.user('operator')
        rule = self.make_rule('res.partner', 'required', ['ref'])
        self.partner('Acme')
        scan = self.make_scan([rule], models=['res.partner'], user=operator)
        scan.action_start()
        self.assertEqual(scan.state, 'queued')
        self.assertEqual(scan.user_id, operator)
        self.assertTrue(scan.date_queued)
        self.drain(scan)
        self.assertEqual(scan.sudo().state, 'done')
        self.assertEqual(scan.sudo().progress, 100.0)
        self.assertGreaterEqual(scan.sudo().records_scanned, 1)

    def test_viewer_has_no_way_to_start_a_scan(self):
        """SC-DGQ-0015-2: the server gives a viewer no right to create or start a scan."""
        viewer = self.user('viewer')
        Scan = self.env['data.quality.scan'].with_user(viewer)
        self.assertFalse(Scan.has_access('create'))
        with self.assertRaises(AccessError):
            Scan.create({})
        rule = self.make_rule('res.partner', 'required', ['ref'])
        scan = self.make_scan([rule], models=['res.partner'])
        with self.assertRaises(AccessError):
            scan.with_user(viewer).action_start()

    def test_empty_scope_means_every_active_rule_of_every_supported_model(self):
        """FR-DGQ-0015: a scan with no scope checks the active rules on the user's allowed companies."""
        company = self.env.company
        active = self.make_rule('res.partner', 'required', ['ref'], company=company)
        paused = self.make_rule('res.partner', 'required', ['vat'], company=company, active=False)
        acme = self.partner('Acme', company)
        scan = self.run_scan(companies=[], models=())
        self.assertEqual(scan.state, 'done')
        self.assertIn(acme.id, self.failing(scan, active))
        self.assertFalse(self.failing(scan, paused))

    def test_scan_has_a_scheduled_action(self):
        """FR-DGQ-0015: the background runner of scans is a scheduled action calling the job runner."""
        Scan = self.env['data.quality.scan']
        self.assertTrue(Scan._dgq_cron_xmlid)
        cron = self.env.ref(Scan._dgq_cron_xmlid)
        self.assertEqual(cron.model_id.model, 'data.quality.scan')
        self.assertIn('_dgq_run_next', cron.code)

    # ---- FR-DGQ-0018 -------------------------------------------------------

    def _two_rules(self):
        return (
            self.make_rule('res.partner', 'required', ['vat'], name='Vendor needs VAT'),
            self.make_rule('res.partner', 'required', ['ref'], name='Contact needs reference'),
        )

    def _refused(self, rules, companies=None):
        with self.assertRaises(UserError) as caught:
            scan = self.make_scan(rules, companies, models=['res.partner'])
            scan.action_start()
        return str(caught.exception)

    def test_same_rule_same_company_is_refused_naming_the_running_scan(self):
        """SC-DGQ-0018-1: a second scan of the rule for the same company is refused, naming the first."""
        vat, _ref = self._two_rules()
        first = self.make_scan([vat], models=['res.partner'])
        first.action_start()
        first.sudo().state = 'running'
        self.assertTrue(first.name)
        self.assertIn(first.name, self._refused([vat]))

    def test_queued_scan_blocks_as_well(self):
        """FR-DGQ-0018: a scan that is only queued already blocks the same scope."""
        vat, _ref = self._two_rules()
        first = self.make_scan([vat], models=['res.partner'])
        first.action_start()
        self.assertEqual(first.state, 'queued')
        self.assertIn(first.name, self._refused([vat]))

    def test_other_rule_or_other_company_is_queued(self):
        """SC-DGQ-0018-2: a scan of another rule, or of the same rule for another company, runs beside it."""
        vat, ref = self._two_rules()
        first = self.make_scan([vat], models=['res.partner'])
        first.action_start()
        first.sudo().state = 'running'
        other_rule = self.make_scan([ref], models=['res.partner'])
        other_rule.action_start()
        self.assertEqual(other_rule.state, 'queued')
        vat_y = self.make_rule('res.partner', 'required', ['vat'], company=self.cy)
        other_company = self.make_scan([vat_y], companies=[self.cy], models=['res.partner'])
        other_company.action_start()
        self.assertEqual(other_company.state, 'queued')

    def test_empty_rule_scope_overlaps_the_rules_it_covers(self):
        """FR-DGQ-0018: a scan of all rules of a company overlaps a running scan of one of its rules."""
        vat, _ref = self._two_rules()
        first = self.make_scan([vat], models=['res.partner'])
        first.action_start()
        self.assertIn(first.name, self._refused([]))

    def test_finished_or_cancelled_scans_do_not_block(self):
        """FR-DGQ-0018: only a scan queued or running blocks; a done or cancelled one does not."""
        vat, _ref = self._two_rules()
        first = self.run_scan([vat], models=['res.partner'])
        self.assertEqual(first.state, 'done')
        second = self.make_scan([vat], models=['res.partner'])
        second.action_start()
        self.assertEqual(second.state, 'queued')
        second.action_cancel()
        third = self.make_scan([vat], models=['res.partner'])
        third.action_start()
        self.assertEqual(third.state, 'queued')

    # ---- FR-DGQ-0020 -------------------------------------------------------

    def _mark(self, field, status):
        return self.env['data.quality.field.config'].create({'field_id': field.id, 'status': status})

    def test_excluded_field_rule_is_reported_not_run(self):
        """SC-DGQ-0020-1: the rule on an excluded field is reported as not run and fails no record."""
        rule = self.make_rule('res.partner', 'required', ['x_dgq_born_on'])
        acme = self.partner('Acme')
        self._mark(self.fld('res.partner', 'x_dgq_born_on'), 'excluded')
        scan = self.run_scan([rule], models=['res.partner'])
        self.assertEqual(scan.state, 'done')
        self.assertFalse(self.failing(scan, rule))
        self.assertNotIn(acme.id, self.failing(scan))
        not_run = dump(scan.rules_not_run)
        self.assertIn(str(rule.id), not_run)
        self.assertIn('exclud', not_run)

    def test_sensitive_field_rule_does_not_run_unless_allowed(self):
        """FR-DGQ-0020: a sensitive field is left out, and read again once an administrator allows it."""
        rule = self.make_rule('product.template', 'required', ['x_dgq_barcode'])
        desk = self.product('Desk')
        mark = self._mark(self.fld('product.template', 'x_dgq_barcode'), 'sensitive')
        scan = self.run_scan([rule], models=['product.template'])
        self.assertFalse(self.failing(scan, rule))
        self.assertIn(str(rule.id), dump(scan.rules_not_run))
        mark.status = 'allowed'
        again = self.run_scan([rule], models=['product.template'])
        self.assertIn(desk.id, self.failing(again, rule))
        self.assertNotIn(str(rule.id), dump(again.rules_not_run))

    def test_excluded_model_is_skipped(self):
        """FR-DGQ-0020: a model marked excluded is skipped and its rules are reported as not run."""
        rule = self.make_rule('product.template', 'required', ['x_dgq_barcode'])
        desk = self.product('Desk')
        config = self.env['data.quality.model.config'].search(
            [('model_id', '=', self.model('product.template').id)])
        config.status = 'excluded'
        scan = self.run_scan([rule], models=['product.template'])
        self.assertEqual(scan.state, 'done')
        self.assertNotIn(desk.id, self.failing(scan, rule))
        self.assertIn(str(rule.id), dump(scan.rules_not_run))
