"""Unit tests of the risk services and guards (T-030).

Requirements: BR-SPM-0017, BR-SPM-0020, FR-SPM-0088, FR-SPM-0089, FR-SPM-0090
"""
from odoo.exceptions import AccessError, UserError
from odoo.tests import Form
from odoo.tests.common import TransactionCase
from odoo.tools import mute_logger
from psycopg2 import IntegrityError

from ..services import risk, supplier_control

MODULE = 'supplier_procurement_management_hub'


class TestRisk(TransactionCase):
    """Requirements: BR-SPM-0017, BR-SPM-0020, FR-SPM-0088, FR-SPM-0089, FR-SPM-0090"""

    def test_level_bands_follow_the_settings(self):
        """BR-SPM-0017: the limits come from the settings, by default 30 and 60."""
        self.assertEqual([risk.level_for(self.env, s) for s in (0, 29.9, 30, 59.9, 60, 100)],
                         ['low', 'low', 'medium', 'medium', 'high', 'high'])
        params = self.env['ir.config_parameter'].sudo()
        params.set_int('spm.risk_medium_from', 10)
        params.set_int('spm.risk_high_from', 20)
        self.assertEqual(risk.level_for(self.env, 15), 'medium')
        self.assertEqual(risk.level_for(self.env, 20), 'high')

    def test_weighted_total(self):
        """BR-SPM-0017: weighted average, 0 without weight."""
        self.assertAlmostEqual(risk.weighted_total([(20, 50), (80, 50)]), 50.0)
        self.assertAlmostEqual(risk.weighted_total([(20, 10), (80, 30)]), 65.0)
        self.assertEqual(risk.weighted_total([(50, 0)]), 0.0)

    def test_result_fields_are_refused_to_a_user(self):
        """FR-SPM-0088: a user cannot write the level of an assessment."""
        manager = self.env['res.users'].create({
            'name': 'M', 'login': 'risk_unit_manager', 'email': 'm@example.com',
            'group_ids': [(6, 0, [self.env.ref(f'{MODULE}.group_spm_manager').id])]})
        partner = self.env['res.partner'].create({'name': 'Gamma'})
        with self.assertRaises(AccessError):
            self.env['procurement.risk.assessment'].with_user(manager).create({
                'partner_id': partner.id, 'level': 'low'})
        assessment = self.env['procurement.risk.assessment'].with_user(manager).create({
            'partner_id': partner.id})
        with self.assertRaises(UserError):
            assessment.action_spm_complete()  # no category

    def test_screening_cannot_be_changed_or_claim_integration(self):
        """FR-SPM-0089: a user's outcome is manual, never changed or deleted."""
        user = self.env['res.users'].create({
            'name': 'B', 'login': 'risk_unit_buyer', 'email': 'b@example.com',
            'group_ids': [(6, 0, [self.env.ref(f'{MODULE}.group_spm_buyer').id])]})
        partner = self.env['res.partner'].create({'name': 'Delta'})
        screening = self.env['procurement.screening'].with_user(user).create({
            'partner_id': partner.id, 'source': 'List', 'result': 'match', 'origin': 'integration'})
        self.assertEqual(screening.origin, 'manual')
        self.assertEqual(screening.user_id, user)
        with self.assertRaises(UserError):
            screening.write({'result': 'clear'})
        with self.assertRaises(UserError):
            screening.unlink()

    def _manager(self, login):
        return self.env['res.users'].create({
            'name': login, 'login': login, 'email': f'{login}@example.com',
            'group_ids': [(6, 0, [self.env.ref(f'{MODULE}.group_spm_manager').id])]})

    def _questionnaire(self, required_questions=2):
        return self.env['procurement.questionnaire'].create({
            'name': 'Risk Q', 'purpose': 'risk',
            'question_ids': [(0, 0, {
                'name': f'Question {index}', 'kind': 'choice', 'required': True,
                'option_ids': [(0, 0, {'name': 'Yes', 'points': 10}), (0, 0, {'name': 'No', 'points': 0})],
            }) for index in range(required_questions)]})

    def test_risk_applies_without_the_status_control_switch(self):
        """FR-SPM-0090: a company's per-level risk settings restrict even with the status control off."""
        partner = self.env['res.partner'].create({'name': 'Eps'})
        partner.spm_risk_level = 'high'
        self.env.company.write({'spm_supplier_control': False, 'spm_risk_high_action': 'block'})
        findings = supplier_control.check(self.env, partner, 'confirm', self.env.company, partner)
        self.assertTrue(findings and findings[0].blocking)
        with self.assertRaises(UserError):
            supplier_control.check_supplier_allowed(self.env, partner, partner, self.env.company, 'confirm')
        self.env.company.spm_risk_high_action = 'none'
        self.assertEqual(supplier_control.check(self.env, partner, 'confirm', self.env.company, partner), [])

    def test_assessment_is_created_from_the_screen(self):
        """FR-SPM-0088: a manager creates an assessment and a category line with the form, which
        sends every field. Form sets fields and saves; no button is pressed here."""
        manager = self._manager('risk_form_manager')
        partner = self.env['res.partner'].create({'name': 'Zeta'})
        category = self.env['procurement.risk.category'].create({'name': 'Fin', 'weight': 1})
        form = Form(self.env['procurement.risk.assessment'].with_user(manager))
        form.partner_id = partner
        with form.category_score_ids.new() as line:
            line.category_id = category
        assessment = form.save()
        self.assertEqual(assessment.state, 'draft')
        self.assertEqual(len(assessment.category_score_ids), 1)

    def test_result_change_is_refused_after_creation(self):
        """FR-SPM-0088: a user cannot move the state, level or a category score by a write."""
        manager = self._manager('risk_write_manager')
        partner = self.env['res.partner'].create({'name': 'Eta'})
        category = self.env['procurement.risk.category'].create({'name': 'Fin', 'weight': 1})
        assessment = self.env['procurement.risk.assessment'].with_user(manager).create({
            'partner_id': partner.id, 'category_score_ids': [(0, 0, {'category_id': category.id})]})
        with self.assertRaises(AccessError):
            assessment.write({'state': 'done'})
        with self.assertRaises(AccessError):
            assessment.category_score_ids.write({'score': 5.0})
        assessment.write({'state': 'draft'})  # a value the record already holds is no change

    def test_complete_refuses_unanswered_required_questions(self):
        """FR-SPM-0088, BR-SPM-0017: an unanswered required question is not no risk."""
        manager = self._manager('risk_missing_manager')
        partner = self.env['res.partner'].create({'name': 'Theta'})
        questionnaire = self._questionnaire()
        category = self.env['procurement.risk.category'].create({
            'name': 'Fin', 'weight': 1, 'questionnaire_id': questionnaire.id})
        first = questionnaire.question_ids[0]
        assessment = self.env['procurement.risk.assessment'].with_user(manager).create({
            'partner_id': partner.id, 'category_score_ids': [(0, 0, {
                'category_id': category.id,
                'answer_ids': [(0, 0, {'question_id': first.id, 'option_id': first.option_ids[0].id})]})]})
        with self.assertRaises(UserError) as ctx:
            assessment.action_spm_complete()
        self.assertIn('Question 1', str(ctx.exception))
        self.assertNotIn('Question 0', str(ctx.exception))
        self.assertEqual(assessment.state, 'draft')

    def test_one_answer_per_question(self):
        """FR-SPM-0088: a question is answered once in a category."""
        partner = self.env['res.partner'].create({'name': 'Iota'})
        questionnaire = self._questionnaire(1)
        category = self.env['procurement.risk.category'].create({
            'name': 'Fin', 'weight': 1, 'questionnaire_id': questionnaire.id})
        question = questionnaire.question_ids
        assessment = self.env['procurement.risk.assessment'].create({
            'partner_id': partner.id, 'category_score_ids': [(0, 0, {'category_id': category.id})]})
        answer = {'question_id': question.id, 'option_id': question.option_ids[0].id}
        assessment.category_score_ids.write({'answer_ids': [(0, 0, answer)]})
        with self.assertRaises(IntegrityError), mute_logger('odoo.sql_db'), self.env.cr.savepoint():
            assessment.category_score_ids.write({'answer_ids': [(0, 0, answer)]})

    def test_missing_document_is_listed_in_the_evidence(self):
        """FR-SPM-0088: a required document that is missing shows as Missing in the evidence."""
        document_type = self.env['procurement.document.type'].create({'name': 'Insurance cert'})
        supplier_type = self.env['procurement.supplier.type'].create({
            'name': 'Risky', 'required_document_type_ids': [(6, 0, document_type.ids)]})
        partner = self.env['res.partner'].create({'name': 'Kappa', 'spm_supplier_type_id': supplier_type.id})
        category = self.env['procurement.risk.category'].create({
            'name': 'Compliance', 'weight': 1, 'uses_documents': True})
        assessment = self.env['procurement.risk.assessment'].create({
            'partner_id': partner.id, 'category_score_ids': [(0, 0, {'category_id': category.id})]})
        assessment.action_spm_complete()
        self.assertEqual(assessment.score, 100.0)
        self.assertIn('Insurance cert: Missing', assessment.category_score_ids.evidence)

    def test_screening_label_shows_the_result_before_the_note(self):
        """BR-SPM-0020: a note never replaces the recorded result in the label."""
        partner = self.env['res.partner'].create({'name': 'Lambda'})
        screening = self.env['procurement.screening'].create({
            'partner_id': partner.id, 'source': 'EU list', 'result': 'match',
            'note': 'cleared by phone'})
        self.assertIn('EU list: Match (cleared by phone), manually recorded by', screening.display_name)

    def test_warning_is_noted_once(self):
        """FR-SPM-0090: confirming then approving one order posts the risk warning once."""
        partner = self.env['res.partner'].create({'name': 'Mu', 'spm_risk_level': 'medium'})
        self.env.company.spm_risk_medium_action = 'warn'
        order = self.env['purchase.order'].create({'partner_id': partner.id})
        supplier_control.check_supplier_allowed(self.env, order, partner, self.env.company, 'confirm')
        supplier_control.check_supplier_allowed(
            self.env, order, partner, self.env.company, 'confirm', warn=False)
        notes = order.message_ids.filtered(lambda m: 'risk level' in (m.body or ''))
        self.assertEqual(len(notes), 1)

    def test_complete_refuses_a_category_without_a_source(self):
        """FR-SPM-0088, BR-SPM-0017: a category with nothing to score is not no risk."""
        manager = self._manager('risk_source_manager')
        partner = self.env['res.partner'].create({'name': 'Iota'})
        optional = self.env['procurement.questionnaire'].create({
            'name': 'Optional Q', 'purpose': 'risk', 'question_ids': [(0, 0, {
                'name': 'Q', 'kind': 'choice', 'required': False,
                'option_ids': [(0, 0, {'name': 'Yes', 'points': 10})]})]})
        with_questions = self.env['procurement.risk.category'].create({
            'name': 'Optional', 'weight': 1, 'questionnaire_id': optional.id})
        empty = self.env['procurement.risk.category'].create({'name': 'Empty', 'weight': 1})
        for category in (with_questions, empty):
            assessment = self.env['procurement.risk.assessment'].with_user(manager).create({
                'partner_id': partner.id, 'category_score_ids': [(0, 0, {'category_id': category.id})]})
            with self.assertRaises(UserError) as ctx:
                assessment.action_spm_complete()
            self.assertIn(category.name, str(ctx.exception))
            self.assertEqual(assessment.state, 'draft')

    def test_event_override_names_a_supplier_of_the_event(self):
        """FR-SPM-0090: a manager overrides a risk refusal of an event for one of its suppliers,
        through the reason dialog (Form); the Override button is left to the screen review."""
        manager = self._manager('risk_event_manager')
        invited = self.env['res.partner'].create({'name': 'Invited', 'spm_risk_level': 'high'})
        stranger = self.env['res.partner'].create({'name': 'Stranger', 'spm_risk_level': 'high'})
        self.env.company.spm_risk_high_action = 'block'
        event = self.env['procurement.sourcing.event'].create({
            'title': 'Event', 'invited_partner_ids': [(6, 0, invited.ids)]})
        with self.assertRaises(UserError):
            supplier_control.check_supplier_allowed(self.env, event, invited, event.company_id, 'invite')
        wizard_model = self.env['procurement.reason.wizard'].with_user(manager).with_context(
            default_res_model=event._name, default_res_ids=event.ids, default_action='override')
        form = Form(wizard_model)
        form.override_action = 'invite'
        form.override_partner_id = invited
        form.reason = 'Needed'
        form.save().action_confirm()
        supplier_control.check_supplier_allowed(self.env, event, invited, event.company_id, 'invite')
        with self.assertRaises(UserError):  # the override covers its step only
            supplier_control.check_supplier_allowed(self.env, event, invited, event.company_id, 'award')
        with self.assertRaises(UserError):
            self.env['procurement.override'].with_user(manager).create({
                'res_model': event._name, 'res_id': event.id, 'partner_id': stranger.id,
                'action': 'invite', 'kind': 'risk', 'reason': 'x'})

    def test_override_dialog_refuses_when_nothing_is_refused(self):
        """FR-SPM-0090: the dialog records nothing for a supplier that needs no override."""
        manager = self._manager('risk_none_manager')
        partner = self.env['res.partner'].create({'name': 'Kappa', 'spm_risk_level': 'low'})
        order = self.env['purchase.order'].create({'partner_id': partner.id})
        wizard = self.env['procurement.reason.wizard'].with_user(manager).create({
            'res_model': order._name, 'res_ids': order.ids, 'action': 'override', 'reason': 'Why'})
        with self.assertRaises(UserError):
            wizard.action_confirm()
        self.assertFalse(self.env['procurement.override'].search([('res_id', '=', order.id)]))

    def test_confirmation_notes_the_warning_once(self):
        """FR-SPM-0090: the native confirm path (confirm, then approve) posts one warning."""
        partner = self.env['res.partner'].create({'name': 'Nu', 'spm_risk_level': 'medium'})
        self.env.company.spm_risk_medium_action = 'warn'
        product = self.env['product.product'].create({'name': 'Nu part', 'type': 'consu'})
        order = self.env['purchase.order'].create({
            'partner_id': partner.id,
            'order_line': [(0, 0, {'product_id': product.id, 'product_qty': 1, 'price_unit': 1})]})
        order.button_confirm()
        notes = order.message_ids.filtered(lambda m: 'risk level' in (m.body or ''))
        self.assertEqual(len(notes), 1)

    def test_warning_repeats_after_a_rolled_back_attempt(self):
        """FR-SPM-0090: a rolled back confirmation leaves no mark; the next one warns again."""
        partner = self.env['res.partner'].create({'name': 'Xi', 'spm_risk_level': 'medium'})
        self.env.company.spm_risk_medium_action = 'warn'
        order = self.env['purchase.order'].create({'partner_id': partner.id})
        with self.assertRaises(UserError), self.cr.savepoint():
            supplier_control.check_supplier_allowed(self.env, order, partner, self.env.company, 'confirm')
            raise UserError('retry')
        supplier_control.check_supplier_allowed(self.env, order, partner, self.env.company, 'confirm')
        supplier_control.check_supplier_allowed(self.env, order, partner, self.env.company, 'confirm')
        notes = order.message_ids.filtered(lambda m: 'risk level' in (m.body or ''))
        self.assertEqual(len(notes), 2)
