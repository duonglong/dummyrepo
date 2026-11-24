"""Records without a name field show a readable name, not the technical model name and id.

Requirements: FR-SPM-0086, FR-SPM-0088
"""
from freezegun import freeze_time

from .comparison_common import SpmComparisonCase
from .matching_common import SpmMatchingCommon
from .scorecard_common import SpmScorecardCase


class TestReviewDisplayNames(SpmScorecardCase):
    """Requirements: FR-SPM-0086, FR-SPM-0088"""

    def _review(self, kind='incident', state='done', date='2026-11-03'):
        return self.env['procurement.supplier.review'].create({
            'partner_id': self.acme.id, 'kind': kind, 'state': state, 'date': date})

    def test_bug_b_0266_a_review_is_named_by_kind_supplier_and_date(self):
        """Regression test for B-0266: a review shows its kind, supplier and date.

        Requirements: FR-SPM-0086
        It cannot reach the rendered picker or breadcrumb: it reads the display name they show.
        """
        review = self._review()
        self.assertEqual(review.display_name, "Incident - Acme - 2026-11-03")
        self.assertEqual(self._review(kind='review', state='to_plan', date=False).display_name, "Review meeting - Acme")

    def test_bug_b_0266_corrective_action_picker_lists_readable_reviews(self):
        """Regression test for B-0266: the review picker of a corrective action lists readable names.

        Requirements: FR-SPM-0086
        """
        review = self._review()
        names = dict(self.env['procurement.supplier.review'].name_search(''))
        self.assertEqual(names[review.id], "Incident - Acme - 2026-11-03")

    def test_bug_b_0266_risk_evidence_names_the_incident(self):
        """Regression test for B-0266: the risk evidence line names the incident, not its technical id.

        Requirements: FR-SPM-0088
        """
        self._review()
        category = self.env['procurement.risk.category'].create({
            'name': 'Delivery', 'weight': 1, 'uses_documents': True, 'uses_incidents': True})
        assessment = self.env['procurement.risk.assessment'].create({
            'partner_id': self.acme.id, 'category_score_ids': [(0, 0, {'category_id': category.id})]})
        assessment.action_spm_complete()
        self.assertIn("Incident: Incident - Acme - 2026-11-03", assessment.category_score_ids.evidence)

    def test_bug_b_0266_sibling_evaluation_is_named_by_supplier_and_date(self):
        """Regression test for B-0266 (same mistake): an evaluation shows its supplier and date.

        Requirements: FR-SPM-0085
        """
        criterion = self.env['procurement.evaluation.criterion'].create({'name': 'Cooperation'})
        with freeze_time('2026-11-17 09:00:00'):
            evaluation = self.env['procurement.supplier.evaluation'].with_user(self.buyer).create({
                'partner_id': self.acme.id, 'date': '2026-11-17',
                'rating_ids': [(0, 0, {'criterion_id': criterion.id, 'rating': '4'})]})
        self.assertEqual(evaluation.display_name, "Evaluation - Acme - 2026-11-17")

    def test_bug_b_0266_sibling_job_run_is_named_by_job_and_start(self):
        """Regression test for B-0266 (same mistake): a job run shows its job and start.

        Requirements: FR-SPM-0096
        """
        job = self.env['procurement.job'].search([], limit=1)
        run = self.env['procurement.job.run'].create({
            'job_id': job.id, 'state': 'done', 'date_start': '2026-11-17 09:00:00', 'trigger': 'manual'})
        self.assertIn(job.name, run.display_name)
        self.assertIn("2026-11-17", run.display_name)

    def test_bug_b_0266_sibling_commitment_and_proposal_name_the_order_line(self):
        """Regression test for B-0266 (same mistake): a commitment and its proposal show the order line.

        Requirements: FR-SPM-0055, FR-SPM-0058
        """
        order = self._dorder()
        commitment = self._commitment(order)
        self.assertIn(order.name, commitment.display_name)
        self.assertNotIn('procurement.delivery.commitment', commitment.display_name)
        order.with_user(self.acme_user).spm_acknowledge({})
        proposal = commitment.with_user(self.acme_user).spm_propose('Shortage', date='2026-12-19 08:00:00')
        self.assertIn(order.name, proposal.display_name)
        self.assertNotIn('procurement.delivery.proposal', proposal.display_name)


class TestMatchingDisplayNames(SpmMatchingCommon):
    """Requirements: FR-SPM-0064"""

    def test_bug_b_0266_sibling_reconciliation_and_line_name_the_bill(self):
        """Regression test for B-0266 (same mistake): a matching and its line show the vendor bill.

        Requirements: FR-SPM-0064
        """
        bill = self._bill(self._order(), 100.0, 11.5)
        recon = self._recon(bill)
        self.assertIn(bill.display_name, recon.display_name)
        self.assertNotIn('procurement.reconciliation', recon.display_name)
        self.assertNotIn('procurement.reconciliation.line', recon.line_ids[:1].display_name)
        self.assertTrue(recon.line_ids[:1].display_name)


class TestComparisonDisplayNames(SpmComparisonCase):
    """Requirements: FR-SPM-0039"""

    def test_bug_b_0266_sibling_comparison_names_its_event(self):
        """Regression test for B-0266 (same mistake): a comparison shows its sourcing event.

        Requirements: FR-SPM-0039
        """
        event = self._sourcing(self.acme | self.beta)
        comparison = self._compare(event)
        self.assertIn(event.display_name, comparison.display_name)
        self.assertNotIn('procurement.comparison', comparison.display_name)


class TestNewRecordDisplayNames(SpmScorecardCase):
    """Requirements: FR-SPM-0086"""

    def test_bug_b_0269_new_review_form_opens(self):
        """Regression test for B-0269: the form of a new review asks for its display name without error.

        Requirements: FR-SPM-0086
        It cannot reach the rendered form: it runs the onchange the form sends when it opens.
        """
        review = self.env['procurement.supplier.review']
        spec = {'display_name': {}, 'partner_id': {}, 'kind': {}, 'date': {}}
        review.onchange({}, [], spec)
        review.onchange({'kind': False}, ['kind'], spec)

    def test_bug_b_0269_new_review_without_kind_or_supplier_is_named(self):
        """Regression test for B-0269: a review missing its kind, supplier or date shows only what is set.

        Requirements: FR-SPM-0086
        """
        review = self.env['procurement.supplier.review']
        self.assertEqual(review.new({'partner_id': self.acme.id, 'kind': False}).display_name, "Acme")
        self.assertEqual(review.new({'kind': 'incident'}).display_name, "Incident")

    def test_bug_b_0269_sibling_new_records_never_show_false(self):
        """Regression test for B-0269 (same mistake): a new record of a model with a display name shows no 'False'.

        Requirements: FR-SPM-0085, FR-SPM-0096, FR-SPM-0055, BR-SPM-0020
        """
        for model in ('procurement.supplier.evaluation', 'procurement.job.run',
                      'procurement.delivery.commitment', 'procurement.delivery.proposal',
                      'procurement.screening', 'procurement.comparison', 'procurement.reconciliation',
                      'procurement.reconciliation.line'):
            with self.subTest(model=model):
                self.assertNotIn('False', self.env[model].new({}).display_name)


class TestScorecardDisplayNames(SpmScorecardCase):
    """Requirements: FR-SPM-0083"""

    def test_bug_b_0275_scorecard_is_named_by_supplier_and_period(self):
        """Regression test for B-0275: a scorecard shows its supplier and period, not its technical name.

        Requirements: FR-SPM-0083
        It cannot reach the rendered title or breadcrumb: it reads the display name they show.
        """
        template = self._template([('on_time', 100.0)])
        scorecard = self.env['procurement.supplier.scorecard'].create({
            'partner_id': self.acme.id, 'template_id': template.id, 'company_id': self.company.id,
            'period_start': '2026-11-01', 'period_end': '2026-11-30'})
        self.assertEqual(scorecard.display_name, "Acme - 2026-11-01 to 2026-11-30")

    def test_bug_b_0275_sibling_records_with_a_form_never_show_the_technical_name(self):
        """Regression test for B-0275 (same mistake): a record that opens in a form is named, even when new.

        Requirements: FR-SPM-0083, FR-SPM-0008, FR-SPM-0035, FR-SPM-0088, FR-SPM-0009
        """
        for model in ('procurement.supplier.scorecard', 'procurement.supplier.document',
                      'procurement.sourcing.response', 'procurement.sourcing.response.version',
                      'procurement.risk.assessment', 'procurement.sourcing.question',
                      'procurement.supplier.assessment', 'procurement.audit.log'):
            with self.subTest(model=model):
                name = self.env[model].new({}).display_name
                self.assertNotIn(model, name)
                self.assertNotIn('False', name)

    def test_bug_b_0275_sibling_risk_assessment_is_named_by_supplier_and_date(self):
        """Regression test for B-0275 (same mistake): a risk assessment shows its supplier and date.

        Requirements: FR-SPM-0088
        """
        assessment = self.env['procurement.risk.assessment'].create({
            'partner_id': self.acme.id, 'date': '2026-11-17'})
        self.assertEqual(assessment.display_name, "Acme - 2026-11-17")

    def test_bug_b_0275_sibling_supplier_document_is_named_by_type_and_supplier(self):
        """Regression test for B-0275 (same mistake): a supplier document shows its type and supplier.

        Requirements: FR-SPM-0008
        """
        document_type = self.env['procurement.document.type'].create({'name': 'Licence B0275'})
        document = self.env['procurement.supplier.document'].create({
            'partner_id': self.acme.id, 'document_type_id': document_type.id})
        self.assertEqual(document.display_name, "Licence B0275 - Acme")


class TestResponseDisplayNames(SpmComparisonCase):
    """Requirements: FR-SPM-0035"""

    def test_bug_b_0275_sibling_response_and_version_name_the_supplier_and_event(self):
        """Regression test for B-0275 (same mistake): a quotation response and its version are named.

        Requirements: FR-SPM-0035
        """
        event = self._sourcing(self.acme | self.beta)
        response = event.response_ids.filtered(lambda r: r.partner_id == self.acme)
        self.assertEqual(response.display_name, f"Acme - {event.display_name}")
        version = self._quote(response, [(event.line_ids, 10.0, 5)])
        self.assertEqual(version.display_name, f"Acme - {event.display_name} - v1")


class TestExceptionDisplayNames(SpmMatchingCommon):
    """Requirements: FR-SPM-0076"""

    def setUp(self):
        super().setUp()
        self._clear_config()

    def test_bug_b_0281_an_exception_is_named_by_its_bill_and_kind(self):
        """Regression test for B-0281: a matching exception shows its bill and kind, not its technical name.

        Requirements: FR-SPM-0076
        It cannot reach the rendered title or breadcrumb: it reads the display name they show.
        """
        bill = self._bill(self._order(receive=100), 100, 12.5)
        exception = self._recon(bill).exception_ids
        self.assertEqual(exception.kind, 'price')
        self.assertEqual(exception.display_name, f"{bill.display_name} - Price")

    def test_bug_b_0281_sibling_records_with_a_form_are_named_even_when_new(self):
        """Regression test for B-0281 (same mistake): the other records that open in a form without a name
        field of their own show no technical name and no 'False', even when new.

        Requirements: FR-SPM-0076, FR-SPM-0050, FR-SPM-0057, FR-SPM-0060
        """
        for model in ('procurement.reconciliation.exception', 'procurement.approval.delegation',
                      'procurement.delivery.discrepancy', 'procurement.delivery.event'):
            with self.subTest(model=model):
                name = self.env[model].new({}).display_name
                self.assertNotIn(model, name)
                self.assertNotIn('False', name)

    def test_bug_b_0281_sibling_delegation_is_named_by_delegator_and_delegate(self):
        """Regression test for B-0281 (same mistake): a delegation shows who gave it and to whom.

        Requirements: FR-SPM-0050
        """
        delegation = self.env['procurement.approval.delegation'].new({
            'delegator_id': self.user_ann.id, 'delegate_id': self.user_fin.id})
        self.assertEqual(delegation.display_name, f"{self.user_ann.name} - {self.user_fin.name}")
