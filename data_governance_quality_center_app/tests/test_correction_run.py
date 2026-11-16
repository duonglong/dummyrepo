from unittest.mock import patch

from odoo import Command
from odoo.exceptions import UserError

from odoo.addons.data_governance_quality_center_app import const
from odoo.addons.data_governance_quality_center_app.services import correction as engine

from .acceptance_m2b_helpers import M2BCase, ROLLBACK, call_first


class TestCorrectionRun(M2BCase):
    """Unit tests of the bulk run, the protected records, the rollback and the country mapping.

    Requirements: BR-DGQ-0005, BR-DGQ-0007, FR-DGQ-0049, FR-DGQ-0050, FR-DGQ-0051, FR-DGQ-0052
    """

    def rollback(self, run, user=None):
        call_first(run.with_user(user or self.manager_a), ROLLBACK)
        rollback = self.env['data.quality.correction.run'].sudo().search([('rollback_of_id', '=', run.id)])  # sudo: the test reads the rollback run of any user
        return self.drain_job(rollback)

    def country_run(self, texts, mappings=()):
        partners = [self.partner('P %s' % text, city=text) for text in texts]
        correction = self.correction(
            'res.partner', 'city', ['country'], domain_ids=[p.id for p in partners],
            mapping_ids=[Command.create({'source_text': text, 'country_id': country.id})
                         for text, country in mappings])
        return partners, self.dry_run(correction)

    def test_country_text_is_set_by_code_name_or_mapping_and_skipped_when_unsure(self):
        """FR-DGQ-0052: 'DE' and 'UK' become a country; a value with several matches or none is skipped."""
        uk = self.env.ref('base.uk')
        us = self.env.ref('base.us')
        partners, run = self.country_run(
            ['DE', 'UK', 'Georgia', 'Nowhere'], mappings=[('UK', uk), ('Georgia', us)])
        de, gb, georgia, nowhere = partners
        self.assertEqual(self.line_of(run, de, 'would_change').value_after, self.env.ref('base.de').id)
        self.assertEqual(self.line_of(run, gb, 'would_change').value_after, uk.id)
        self.assertIn('several', self.line_of(run, georgia, 'skipped').reason)
        self.assertTrue(self.line_of(run, nowhere, 'skipped'))
        self.assertEqual((run.count_changed, run.count_skipped), (2, 2))

    def test_a_state_text_matches_the_states_of_the_country_of_the_record(self):
        """FR-DGQ-0052: 'california' is California for a record of the United States, never guessed elsewhere."""
        us = self.env.ref('base.us')
        partner = self.partner('Californian', country_id=us.id, city='California')
        self.assertEqual(
            engine.action_country_state(
                'california', partner, {'ttype': 'char', 'relation': 'res.country.state', 'field_name': 'state',
                                        'mappings': {}, 'cache': {}, 'write_link': True}),
            partner.env.ref('base.state_us_5').id)

    def test_accounting_models_are_protected_by_name(self):
        """BR-DGQ-0005: a model of the accounting list is protected, needs approval and is skipped."""
        partner = self.partner('Ref', ref=' R1 ')
        with patch.object(const, 'PROTECTED_MODELS', ('res.partner',)):
            correction = self.correction('res.partner', 'ref', ['trim'], domain_ids=partner.ids)
            self.assertTrue(correction.needs_approval)
            self.dry_run(correction)
            self.approve(correction, self.manager_b)
            run = self.real_run(correction)
        partner.invalidate_recordset()
        self.assertEqual(partner.ref, ' R1 ')
        self.assertEqual(run.count_skipped, 1)

    def test_a_run_rolled_back_twice_is_refused(self):
        """FR-DGQ-0050: a run is rolled back once; the second request is refused."""
        partner = self.partner('Ref', ref=' R1 ')
        correction = self.correction('res.partner', 'ref', ['trim'], domain_ids=partner.ids)
        self.dry_run(correction)
        run = self.real_run(correction)
        self.rollback(run)
        run.invalidate_recordset()
        self.assertTrue(run.rolled_back)
        with self.assertRaises(UserError):
            run.with_user(self.manager_a).action_rollback()

    def test_the_rollback_of_a_deleted_record_is_listed_not_restored(self):
        """BR-DGQ-0007: a record deleted since the run cannot be restored and is listed."""
        partner = self.partner('Ref', ref=' R1 ')
        correction = self.correction('res.partner', 'ref', ['trim'], domain_ids=partner.ids)
        self.dry_run(correction)
        run = self.real_run(correction)
        partner.unlink()
        rollback = self.rollback(run)
        self.assertEqual((rollback.count_restored, rollback.count_not_restored), (0, 1))

    def test_a_refused_write_is_logged_with_the_error(self):
        """FR-DGQ-0049: the log of a failed record names the error."""
        partner = self.partner('Ref', ref=' R1 ')
        correction = self.correction('res.partner', 'ref', ['trim'], domain_ids=partner.ids)
        self.dry_run(correction)
        Partner = type(self.env['res.partner'])
        original = Partner.write

        def refuse(this, vals):
            if 'ref' in vals:
                raise UserError('Refused by the test')
            return original(this, vals)

        with patch.object(Partner, 'write', refuse):
            run = self.real_run(correction)
        self.assertIn('Refused by the test', self.line_of(run, partner, 'failed').reason)

    def test_a_state_text_of_a_record_without_country_is_skipped(self):
        """FR-DGQ-0052: with no country on the record no state is guessed, so 'Georgia' is skipped."""
        partner = self.partner('Stateless', city='Georgia')
        correction = self.correction('res.partner', 'city', ['country'], domain_ids=partner.ids)
        params = engine.action_params(correction)
        params['relation'] = 'res.country.state'
        with self.assertRaises(engine.DgqSkip):
            engine.action_country_state('Georgia', partner, params)

    def test_a_country_text_of_a_model_without_country_link_is_written_as_a_name(self):
        """FR-DGQ-0052: a text field of a model with no country link keeps text, never an id."""
        industry = self.env['res.partner.industry'].create({'name': 'X', 'full_name': 'germany'})
        correction = self.correction('res.partner.industry', 'full_name', ['country'], domain_ids=industry.ids)
        run = self.dry_run(correction)
        self.assertEqual(self.line_of(run, industry, 'would_change').value_after, 'Germany')

    def test_rollback_of_a_sensitive_field_keeps_no_value(self):
        """BR-DGQ-0007: a masked line is listed as not restored and no rollback line holds a value."""
        partner = self.partner('Sensitive', ref=' R1 ')
        correction = self.correction('res.partner', 'ref', ['trim'], domain_ids=partner.ids)
        with patch.object(type(correction), '_dgq_is_sensitive', lambda self: True):
            self.dry_run(correction)
            self.approve(correction, self.manager_b)
            run = self.real_run(correction)
            self.assertTrue(self.line_of(run, partner, 'changed').masked)
            rollback = self.rollback(run)
        self.assertEqual((rollback.count_restored, rollback.count_not_restored), (0, 1))
        line = self.line_of(rollback, partner, 'not_restored')
        self.assertFalse(line.value_before or line.value_after)
        self.assertEqual(partner.ref, 'R1')

    def test_rollback_of_a_trim_to_an_empty_value_restores_the_spaces(self):
        """BR-DGQ-0007: an emptied value is stored as NULL and still counts as the value the run wrote."""
        partner = self.partner('Blank', ref='   ')
        correction = self.correction('res.partner', 'ref', ['trim'], domain_ids=partner.ids)
        self.dry_run(correction)
        run = self.real_run(correction)
        rollback = self.rollback(run)
        self.assertEqual((rollback.count_restored, rollback.count_not_restored), (1, 0))
        partner.invalidate_recordset()
        self.assertEqual(partner.ref, '   ')
