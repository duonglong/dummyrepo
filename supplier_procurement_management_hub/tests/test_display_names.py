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
