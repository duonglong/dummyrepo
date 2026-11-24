"""Extension points: a module adds a supplier field, a criterion, a scorecard metric or an approver type.

Requirements: NFR-SPM-0010
The README section "Extension points" says how; these tests prove each way works with the code
of the app as it stands, and that the README names every hook.
"""
from datetime import date
from pathlib import Path
from unittest.mock import patch

from ..services import performance
from .scorecard_common import SpmScorecardCase

README = Path(__file__).resolve().parent.parent / 'README.md'


class TestExtensionPoints(SpmScorecardCase):
    """Requirements: NFR-SPM-0010"""

    def test_nfr_0010_a_supplier_field_is_added_without_code(self):
        """NFR-SPM-0010: a contact field becomes a portal field with one record, and field keys extend by selection."""
        field = self.env['ir.model.fields']._get('res.partner', 'function')
        portal_field = self.env['procurement.portal.field'].create({'field_id': field.id})
        self.assertEqual(portal_field.field_id.name, 'function')
        keys = self.env['procurement.supplier.type.field']._fields['field_key']
        self.assertTrue(keys.selection)

    def test_nfr_0010_a_criterion_is_added_without_code(self):
        """NFR-SPM-0010: an evaluation criterion and a weighted sourcing criterion are records."""
        criterion = self.env['procurement.evaluation.criterion'].create({'name': 'Innovation own'})
        self.assertTrue(criterion.active)
        weighted = self.env['procurement.sourcing.criterion'].create({
            'name': 'Sustainability own', 'event_id': self._event(self.acme, send=False).id,
            'kind': 'manual', 'weight': 2.0})
        self.assertEqual(weighted.kind, 'manual')

    def test_nfr_0010_a_scorecard_metric_is_computed_by_the_table_of_computers(self):
        """NFR-SPM-0010: the evaluation of a template calls what the table of computers holds for the kind."""
        template = self._template([('quality', 1.0)])

        def custom(env, partner, company, start, end):
            return performance._result(env, 'quality', 1.0, 1.0, value=77.0)
        metric_model = self.registry['procurement.scorecard.template.metric']
        original = metric_model._spm_metric_computers
        with patch.object(metric_model, '_spm_metric_computers',
                          lambda self: dict(original(self), quality=custom)):
            result = performance.evaluate(template, self.acme, date(2026, 10, 1), date(2026, 10, 31))
        self.assertEqual(result['score'], 77.0)

    def test_nfr_0010_an_approver_type_is_resolved_by_the_table_of_resolvers(self):
        """NFR-SPM-0010: the approvers of a step come from the method the table names for its type."""
        policy = self.env['procurement.approval.policy'].create({'name': 'Extension policy'})
        step = self.env['procurement.approval.policy.step'].create({
            'policy_id': policy.id, 'name': 'Custom', 'approver_type': 'group',
            'group_id': self.env.ref('base.group_user').id})
        order = self.env['purchase.order'].create({'partner_id': self.acme.id})
        step_model = self.registry['procurement.approval.policy.step']
        original = step_model._spm_approver_resolvers
        buyer = self.buyer
        with patch.object(step_model, '_spm_approver_resolvers',
                          lambda self: dict(original(self), group='_spm_resolve_custom_own')), \
                patch.object(step_model, '_spm_resolve_custom_own', lambda self, order: buyer, create=True):
            self.assertEqual(step.spm_resolve_approvers(order), [buyer.id])

    def test_nfr_0010_the_readme_names_each_extension_point(self):
        """NFR-SPM-0010: the README documents the four ways and the hooks they use."""
        text = README.read_text()
        for needle in ('Extension points', 'procurement.portal.field', 'procurement.evaluation.criterion',
                       '_spm_metric_computers', '_spm_approver_resolvers', 'selection_add'):
            self.assertIn(needle, text)


class TestEditionTranslationPhases(SpmScorecardCase):
    """Requirements: NFR-SPM-0009, NFR-SPM-0011, NFR-SPM-0013"""

    def test_nfr_0011_every_dependency_is_a_community_module(self):
        """NFR-SPM-0011: no module of the dependency tree carries the Enterprise licence."""
        module = self.env['ir.module.module'].search([('name', '=', 'supplier_procurement_management_hub')])
        tree = module.upstream_dependencies(exclude_states=())
        self.assertTrue(tree)
        self.assertFalse(tree.filtered(lambda dependency: dependency.license == 'OEEL-1'))
        self.assertFalse(module.license == 'OEEL-1')

    def test_nfr_0009_the_translation_template_holds_the_source_texts(self):
        """NFR-SPM-0009: user-facing texts are translatable source, exported in the module's template."""
        template = (README.parent / 'i18n' / 'supplier_procurement_management_hub.pot').read_text()
        self.assertIn('msgid "Supplier status"', template)
        self.assertIn('msgstr ""', template)

    def test_nfr_0013_the_readme_lists_the_seven_phases(self):
        """NFR-SPM-0013: the delivery order of the seven phases is written down."""
        text = ' '.join(README.read_text().lower().split())
        for phase in ('onboarding and security', 'portal and quotes', 'comparison and approvals',
                      'contracts and delivery', 'matching', 'scorecards, risk and analytics',
                      'automation and hardening'):
            self.assertIn(phase, text)
