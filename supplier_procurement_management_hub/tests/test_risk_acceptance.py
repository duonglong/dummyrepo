"""Acceptance tests of T-030 (supplier risk: explainable scoring, honest screening,
restriction by risk level), written from the specification.

Requirements: BR-SPM-0017, BR-SPM-0020, FR-SPM-0088, FR-SPM-0089, FR-SPM-0090

Names not fixed by the design are chosen here and recorded in the module notes:
- procurement.risk.assessment.action_spm_complete() is the Complete button (draft to done);
  its category scores are created with it as one2many commands (category_id and the answers,
  question_id and option_id, as ``answer_ids`` commands; the compliance category reads the
  supplier's documents on its own); the risk level values are low, medium and high;
- a screening shows as ``display_name``: source, result, "manually recorded by <user> on <date>";
- services.supplier_control.check(env, partners, action, company, record) returns a list whose
  items name the risk level (its str() holds the level name): the refusals and the warnings;
- the override of a risk refusal is a procurement.override of kind ``risk`` (the reason dialog
  is left to the build).
"""
from freezegun import freeze_time

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase

MODULE = 'supplier_procurement_management_hub'


class TestRiskAcceptance(TransactionCase):
    """Requirements: BR-SPM-0017, BR-SPM-0020, FR-SPM-0088, FR-SPM-0089, FR-SPM-0090"""

    def setUp(self):
        super().setUp()
        self.env = self.env(context=dict(self.env.context, tracking_disable=True))
        env = self.env
        self.company = env.company
        companies = self.company.ids

        def user(name, login, *xmlids):
            return env['res.users'].with_context(no_reset_password=True).create({
                'name': name, 'login': login, 'email': f'{login}@example.com',
                'company_id': self.company.id, 'company_ids': [(6, 0, companies)],
                'group_ids': [(6, 0, [env.ref(x).id for x in xmlids]
                                + [env.ref('base.group_partner_manager').id])],
            })
        self.manager = user('Mia Manager', 'spm_manager_t030', f'{MODULE}.group_spm_manager',
                            'purchase.group_purchase_manager')
        self.buyer = user('Ann Buyer', 'spm_buyer_t030', f'{MODULE}.group_spm_buyer')
        approved = env['procurement.supplier.status'].search(
            [('kind', '=', 'approved'), ('default_for_kind', '=', True)], limit=1)
        self.acme = env['res.partner'].create({
            'name': 'Acme', 'is_company': True, 'spm_status_id': approved.id})
        self.beta = env['res.partner'].create({
            'name': 'Beta', 'is_company': True, 'spm_status_id': approved.id})
        self.product = env['product.product'].create({'name': 'Risk part', 'type': 'consu'})
        # only the categories of the test count
        env['procurement.risk.category'].with_context(active_test=False).search([]).write({'active': False})

    # fixtures ----------------------------------------------------------------
    def _questionnaire(self, name, options):
        """A risk questionnaire with one choice question; ``options`` maps a label to its points."""
        questionnaire = self.env['procurement.questionnaire'].create({
            'name': name, 'purpose': 'risk',
            'question_ids': [(0, 0, {
                'name': f'{name} question', 'kind': 'choice',
                'option_ids': [(0, 0, {'name': label, 'points': points})
                               for label, points in options.items()],
            })],
        })
        question = questionnaire.question_ids
        return questionnaire, question, {o.name: o for o in question.option_ids}

    def _category(self, name, weight, questionnaire=None, uses_documents=False):
        return self.env['procurement.risk.category'].create({
            'name': name, 'weight': weight, 'questionnaire_id': questionnaire.id if questionnaire else False,
            'uses_documents': uses_documents})

    def _assessment(self, partner, scores):
        """A draft assessment; ``scores`` is a list of (category, question, option) with the
        question and option False for a category with no answers."""
        return self.env['procurement.risk.assessment'].with_user(self.manager).create({
            'partner_id': partner.id, 'date': '2026-11-17', 'assessed_by_id': self.manager.id,
            'category_score_ids': [(0, 0, dict(
                {'category_id': category.id},
                **({'answer_ids': [(0, 0, {'question_id': question.id, 'option_id': option.id})]}
                   if question else {}))) for category, question, option in scores],
        })

    def _complete(self, assessment):
        assessment.with_user(self.manager).action_spm_complete()
        self.env.flush_all()
        return assessment

    # FR-SPM-0088, BR-SPM-0017 -----------------------------------------------
    def test_sc_0088_1_total_level_and_evidence(self):
        """SC-SPM-0088-1: categories financial weight 50 and compliance weight 50; Acme's financial
        answers score 20 and its expired insurance makes compliance 80; completing the assessment
        gives a total of 50 and the level Medium; the evidence lists the answers and the
        expired insurance; the supplier's risk level is Medium."""
        env = self.env
        questionnaire, question, options = self._questionnaire(
            'Financial', {'Low risk answer': 2.0, 'High risk answer': 10.0})
        financial = self._category('Financial', 50.0, questionnaire)
        compliance = self._category('Compliance', 50.0, uses_documents=True)
        types = env['procurement.document.type'].create(
            [{'name': f'Doc {i} M6', 'needs_expiry': True, 'reminder_days': 30} for i in range(5)])
        supplier_type = env['procurement.supplier.type'].create({
            'name': 'Goods M6', 'required_document_type_ids': [(6, 0, types.ids)]})
        self.acme.write({'spm_supplier_type_id': supplier_type.id})
        # five required documents: the insurance is expired, three are missing, one is valid
        insurance = env['procurement.supplier.document'].create({
            'partner_id': self.acme.id, 'document_type_id': types[0].id,
            'issue_date': '2025-01-01', 'expiry_date': '2026-10-01'})
        env['procurement.supplier.document'].create({
            'partner_id': self.acme.id, 'document_type_id': types[1].id,
            'issue_date': '2026-01-01', 'expiry_date': '2027-12-31', 'verification_state': 'verified'})
        assessment = self._assessment(self.acme, [
            (financial, question, options['Low risk answer']), (compliance, False, False)])
        with freeze_time('2026-11-17 10:00:00'):
            self._complete(assessment)
        self.assertEqual(assessment.state, 'done')
        scores = {s.category_id: s for s in assessment.category_score_ids}
        self.assertAlmostEqual(scores[financial].score, 20.0, places=1)
        self.assertAlmostEqual(scores[compliance].score, 80.0, places=1)
        self.assertAlmostEqual(assessment.score, 50.0, places=1)
        self.assertEqual(assessment.level, 'medium')
        self.assertEqual(len(scores[financial].answer_ids), 1, "the answers are the evidence")
        self.assertIn(insurance, scores[compliance].document_ids)
        self.assertEqual(self.acme.spm_risk_level, 'medium')
        self.assertTrue(assessment.is_current)

    def test_br_0017_level_bands_at_and_around_their_limits(self):
        """BR-SPM-0017: below 30 Low, from 30 to below 60 Medium, from 60 High."""
        questionnaire, question, options = self._questionnaire(
            'Bands', {'a': 29.0, 'b': 30.0, 'c': 59.0, 'd': 60.0, 'z': 100.0})
        category = self._category('Bands', 100.0, questionnaire)
        expected = {'a': 'low', 'b': 'medium', 'c': 'medium', 'd': 'high'}
        for label, level in expected.items():
            partner = self.env['res.partner'].create({'name': f'Band {label}', 'is_company': True})
            assessment = self._complete(self._assessment(partner, [(category, question, options[label])]))
            self.assertEqual(assessment.level, level, f"{options[label].points} points")
            self.assertEqual(partner.spm_risk_level, level)

    def test_fr_0088_a_new_assessment_replaces_the_current_one(self):
        """FR-SPM-0088: the latest done assessment sets the level and the earlier one is no
        longer current."""
        questionnaire, question, options = self._questionnaire('Twice', {'low': 10.0, 'high': 90.0})
        category = self._category('Twice', 100.0, questionnaire)
        first = self._complete(self._assessment(self.acme, [(category, question, options['high'])]))
        second = self._complete(self._assessment(self.acme, [(category, question, options['low'])]))
        first.invalidate_recordset()
        self.assertFalse(first.is_current)
        self.assertTrue(second.is_current)
        self.assertEqual(self.acme.spm_risk_level, 'low')

    # FR-SPM-0089, BR-SPM-0020 -----------------------------------------------
    def test_sc_0089_1_a_screening_is_shown_as_manually_recorded(self):
        """SC-SPM-0089-1: no screening integration is configured; Ann records 'EU sanctions list:
        no match' for Acme on 2026-11-17: Acme shows it as manually recorded by her on that date
        and the word verified is not shown."""
        screening = self.env['procurement.screening'].with_user(self.manager).create({
            'partner_id': self.acme.id, 'kind': 'sanctions', 'source': 'EU sanctions list',
            'result': 'clear', 'date': '2026-11-17', 'note': 'no match'})
        self.env.flush_all()
        self.assertEqual(screening.origin, 'manual')
        self.assertEqual(screening.user_id, self.manager)
        shown = screening.display_name
        self.assertIn('EU sanctions list', shown)
        self.assertIn('manually recorded by Mia Manager on 2026-11-17', shown)
        self.assertNotIn('verified', shown.lower())

    # FR-SPM-0090 ---------------------------------------------------------------
    def _supplier_with_level(self, partner, level):
        partner.sudo().write({'spm_risk_level': level})

    def _order(self, partner):
        return self.env['purchase.order'].create({
            'partner_id': partner.id, 'user_id': self.buyer.id,
            'order_line': [(0, 0, {
                'product_id': self.product.id, 'name': 'Part', 'product_qty': 2.0,
                'price_unit': 5.0, 'tax_ids': [(6, 0, [])]})],
        })

    def _configure_company(self):
        self.company.sudo().write({
            'spm_supplier_control': True, 'spm_risk_high_action': 'block',
            'spm_risk_medium_action': 'warn', 'spm_risk_low_action': 'none'})

    def test_sc_0090_1_a_high_risk_supplier_blocks_the_confirmation(self):
        """SC-SPM-0090-1: company A blocks High and warns on Medium; Acme's risk level is High;
        a Buyer confirming an order with Acme is refused with a message naming the High level."""
        self._configure_company()
        self._supplier_with_level(self.acme, 'high')
        order = self._order(self.acme)
        with self.assertRaises(UserError) as ctx:
            order.with_user(self.buyer).button_confirm()
        self.assertIn('High', str(ctx.exception))
        self.assertEqual(order.state, 'draft')

    def test_sc_0090_2_a_medium_risk_supplier_is_confirmed_with_a_warning(self):
        """SC-SPM-0090-2: Beta's risk level is Medium; a Buyer confirms an order with Beta: it is
        confirmed and the check names the Medium level as a warning."""
        from odoo.addons.supplier_procurement_management_hub.services import supplier_control
        self._configure_company()
        self._supplier_with_level(self.beta, 'medium')
        order = self._order(self.beta)
        findings = supplier_control.check(self.env, self.beta, 'confirm', self.company, order)
        self.assertTrue(any('Medium' in str(item) for item in findings), findings)
        order.with_user(self.buyer).button_confirm()
        self.assertIn(order.state, ('purchase', 'done'))

    def test_fr_0090_a_manager_override_lets_the_blocked_order_through(self):
        """FR-SPM-0090: a Procurement manager records an override with a reason for this order:
        the Buyer's confirmation is no longer refused."""
        self._configure_company()
        self._supplier_with_level(self.acme, 'high')
        order = self._order(self.acme)
        with self.assertRaises(UserError):
            order.with_user(self.buyer).button_confirm()
        self.env['procurement.override'].with_user(self.manager).create({
            'res_model': 'purchase.order', 'res_id': order.id, 'partner_id': self.acme.id,
            'action': 'confirm', 'kind': 'risk', 'reason': 'Only source of the part',
            'company_id': self.company.id})
        order.with_user(self.buyer).button_confirm()
        self.assertIn(order.state, ('purchase', 'done'))

    def test_fr_0090_a_level_set_to_none_is_not_restricted(self):
        """FR-SPM-0090: a Low level configured as none neither warns nor blocks."""
        from odoo.addons.supplier_procurement_management_hub.services import supplier_control
        self._configure_company()
        self._supplier_with_level(self.beta, 'low')
        order = self._order(self.beta)
        self.assertFalse(supplier_control.check(self.env, self.beta, 'confirm', self.company, order))
        order.with_user(self.buyer).button_confirm()
        self.assertIn(order.state, ('purchase', 'done'))
