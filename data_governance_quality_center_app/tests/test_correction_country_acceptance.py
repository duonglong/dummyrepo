from .acceptance_m2b_helpers import M2BCase, dump


class TestCorrectionCountryAcceptance(M2BCase):
    """Acceptance tests of T-020: country standardization from reliable mappings.

    The text field holds the country text of the contact (a manual character field of the test) and
    the result is written to the contact's country, as the administrator answered. The correction's
    form lists its mappings (``mapping_ids``), a line holds a source text and a country; the test
    creates the correction and its mapping lines in one write, the form step is left to the build.
    A dry run changes no record: what it "turns into" is read from its log lines (state
    ``would_change`` with the country in the value after, state ``skipped`` with a reason).

    SC-DGQ-0052-1 says 'Georgia' matches several entries while its only mapping is 'UK'. By name
    Georgia is one country, so the test gives the manager one more mapping, 'Georgia' to United
    States, to make the text match two entries (the country and the mapped one).

    Requirements: FR-DGQ-0052
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.ensure_field('res.partner', 'x_dgq_country_text', 'char', 'DGQ Country Text')

    def setUp(self):
        super().setUp()
        Country = self.env['res.country']
        self.de = Country.search([('code', '=', 'DE')], limit=1)
        self.uk = Country.search([('code', '=', 'GB')], limit=1)
        self.us = Country.search([('code', '=', 'US')], limit=1)
        self.p_de = self.partner('Hans', x_dgq_country_text='DE')
        self.p_uk = self.partner('Alice', x_dgq_country_text='UK')
        self.p_ge = self.partner('Nika', x_dgq_country_text='Georgia')
        self.all = self.p_de | self.p_uk | self.p_ge

    def standardize(self):
        mappings = [
            (0, 0, {'source_text': 'UK', 'country_id': self.uk.id}),
            (0, 0, {'source_text': 'Georgia', 'country_id': self.us.id}),
        ]
        return self.correction(
            'res.partner', 'x_dgq_country_text', ['country'], domain_ids=self.all.ids,
            mapping_ids=mappings)

    def test_a_dry_run_maps_de_and_uk_and_skips_georgia(self):
        """SC-DGQ-0052-1: 'DE' becomes Germany, 'UK' becomes United Kingdom, 'Georgia' is skipped (several matches)."""
        run = self.dry_run(self.standardize())
        self.assertTrue(run, 'the dry run left no run record')
        for partner, country in ((self.p_de, self.de), (self.p_uk, self.uk)):
            line = self.line_of(run, partner, 'would_change')
            self.assertEqual(len(line), 1, 'no would-change line for %s' % partner.name)
            after = dump(line.value_after)
            self.assertTrue(
                country.name.lower() in after or str(country.id) in after,
                '%s: value after is %s' % (partner.name, after))
        skipped = self.line_of(run, self.p_ge, 'skipped')
        self.assertEqual(len(skipped), 1)
        self.assertTrue(skipped.reason)
        self.assertFalse(self.line_of(run, self.p_ge, 'would_change'))
        self.all.invalidate_recordset()
        self.assertFalse(self.all.mapped('country_id'), 'a dry run must not change any record')

    def test_a_real_run_sets_only_the_certain_countries(self):
        """FR-DGQ-0052: after the run Germany and United Kingdom are set; Georgia's contact keeps no country."""
        correction = self.standardize()
        self.dry_run(correction)
        if correction.needs_approval:
            self.approve(correction, self.manager_b)
        self.real_run(correction)
        self.all.invalidate_recordset()
        self.assertEqual(self.p_de.country_id, self.de)
        self.assertEqual(self.p_uk.country_id, self.uk)
        self.assertFalse(self.p_ge.country_id)
