from odoo import Command
from odoo.exceptions import AccessError, UserError

from .acceptance_m3_helpers import M3Case, as_pairs, dump


class TestProfileAcceptance(M3Case):
    """Acceptance tests of T-021: profile completeness, frequencies, formats, unusual numbers, staleness.

    A profile is filled in on its form (model, company, fields), saved, then started with the job
    mixin's ``action_start``; the background runner computes it and each field gets a line of
    ``data.quality.profile.line``. The comparison of two profiles and the exports (T-022) are reported
    as unclear. Shares are accepted as a percentage (25.0) or a fraction (0.25).

    Requirements: FR-DGQ-0053, FR-DGQ-0054, FR-DGQ-0055
    """

    def assertShare(self, share, percent):
        self.assertIn(round(share, 4), (round(percent, 4), round(percent / 100.0, 4)))

    # ---- FR-DGQ-0053 -------------------------------------------------------

    def test_completeness_and_frequencies(self):
        """SC-DGQ-0053-1: 100 contacts, 25 without e-mail, 60 in Belgium and 40 in France."""
        self.isolate('res.partner')
        belgium, france = self.env.ref('base.be'), self.env.ref('base.fr')
        self.env['res.partner'].create([{
            'name': 'Prof %03d' % n, 'company_id': self.cx.id,
            'email': False if n < 25 else 'prof%s@example.com' % n,
            'country_id': (belgium if n < 60 else france).id} for n in range(100)])
        profile = self.make_profile('res.partner', ['email', 'country_id'])
        self.run_profile(profile)
        self.assertEqual(profile.state, 'done')
        self.assertEqual(profile.record_count, 100)
        email = self.line(profile, 'email')
        self.assertEqual(email.empty_count, 25)
        self.assertShare(email.empty_share, 25.0)
        self.assertEqual(email.distinct_count, 75)
        country = self.line(profile, 'country_id')
        self.assertEqual(country.distinct_count, 2)
        top = as_pairs(country.top_values)
        counts = {key.lower(): value for key, value in top.items()}
        self.assertEqual(counts.get('belgium'), 60, dump(country.top_values))
        self.assertEqual(counts.get('france'), 40, dump(country.top_values))

    def test_only_the_top_20_values_are_kept(self):
        """FR-DGQ-0053: of 25 different values the 20 most frequent are kept, with the distinct count of all 25."""
        self.isolate('res.partner')
        self.env['res.partner'].create([
            {'name': 'Top %02d' % n, 'function': 'Role %02d' % n, 'company_id': self.cx.id}
            for n in range(25)] + [{'name': 'Top extra', 'function': 'Role 00', 'company_id': self.cx.id}])
        profile = self.run_profile(self.make_profile('res.partner', ['function']))
        line = self.line(profile, 'function')
        self.assertEqual(line.distinct_count, 25)
        top = as_pairs(line.top_values)
        self.assertEqual(len(top), 20)
        self.assertEqual(top.get('Role 00'), 2)

    def test_the_link_distribution_and_empty_links(self):
        """FR-DGQ-0055: for a link field, the records per linked record and the share of empty links."""
        self.isolate('res.partner')
        belgium = self.env.ref('base.be')
        self.env['res.partner'].create(
            [{'name': 'Link %s' % n, 'company_id': self.cx.id, 'country_id': belgium.id} for n in range(8)]
            + [{'name': 'Nolink %s' % n, 'company_id': self.cx.id} for n in range(2)])
        profile = self.run_profile(self.make_profile('res.partner', ['country_id']))
        line = self.line(profile, 'country_id')
        self.assertShare(line.link_empty_share, 20.0)
        counts = {key.lower(): value for key, value in as_pairs(line.link_distribution).items()}
        self.assertEqual(counts.get('belgium'), 8, dump(line.link_distribution))

    def test_the_profile_is_computed_in_the_background(self):
        """FR-DGQ-0053: starting a profile returns at once; the figures come when the runner has worked."""
        self.isolate('res.partner')
        self.env['res.partner'].create({'name': 'Bg', 'company_id': self.cx.id})
        profile = self.make_profile('res.partner', ['email'])
        profile.action_start()
        self.assertEqual(profile.state, 'queued')
        self.assertFalse(profile.record_count)
        self.drain_job(profile)
        profile.invalidate_recordset()
        self.assertEqual(profile.state, 'done')
        self.assertEqual(profile.record_count, 1)

    def test_a_profile_changes_no_data(self):
        """FR-DGQ-0053: profiling reads the data and never changes it."""
        self.isolate('res.partner')
        partner = self.env['res.partner'].create({'name': 'Keep', 'email': ' Keep@X.com ', 'company_id': self.cx.id})
        self.run_profile(self.make_profile('res.partner', ['email', 'name']))
        partner.invalidate_recordset()
        self.assertEqual((partner.name, partner.email), ('Keep', ' Keep@X.com '))

    def test_an_excluded_field_cannot_be_profiled(self):
        """FR-DGQ-0053: a field marked excluded is refused in a profile."""
        field = self.fld('res.partner', 'function')
        self.env['data.quality.field.config'].sudo().create({'field_id': field.id, 'status': 'excluded'})
        with self.assertRaises(UserError):
            self.env['data.quality.profile'].with_user(self.operator).create({
                'model_id': self.model('res.partner').id, 'company_id': self.cx.id,
                'field_ids': [Command.set([field.id])]})

    def test_a_viewer_cannot_profile(self):
        """FR-DGQ-0053: only operators and above profile a model."""
        Profile = self.env['data.quality.profile'].with_user(self.viewer)
        with self.assertRaises(AccessError):
            Profile.create({'model_id': self.model('res.partner').id, 'company_id': self.cx.id})

    def test_a_profile_counts_its_own_company_only(self):
        """FR-DGQ-0053: a profile of company X does not count the records of company Y."""
        self.isolate('res.partner')
        self.env['res.partner'].create([
            {'name': 'In X %s' % n, 'company_id': self.cx.id} for n in range(3)]
            + [{'name': 'In Y %s' % n, 'company_id': self.cy.id} for n in range(4)])
        profile = self.run_profile(self.make_profile('res.partner', ['name']))
        self.assertEqual(profile.record_count, 3)

    def test_compared_profile_must_be_of_the_same_model_and_company(self):
        """FR-DGQ-0056 (data rule of the profile): the profile to compare with has the same model and company."""
        Profile = self.env['data.quality.profile'].with_user(self.operator)
        base = {'company_id': self.cx.id}
        earlier = Profile.create(dict(base, model_id=self.model('res.partner').id))
        other_model = Profile.create(dict(base, model_id=self.model('product.template').id))
        other_company = Profile.create({'model_id': self.model('res.partner').id, 'company_id': self.cy.id})
        same = Profile.create(dict(base, model_id=self.model('res.partner').id))
        same.compare_profile_id = earlier  # accepted
        for wrong in (other_model, other_company):
            with self.assertRaises(UserError):
                same.compare_profile_id = wrong
        with self.assertRaises(UserError):
            other_model.compare_profile_id = earlier

    # ---- FR-DGQ-0054 -------------------------------------------------------

    def test_value_shapes_of_a_text_field(self):
        """SC-DGQ-0054-1: postcodes '1000', '1050', 'B-1000', 'B1000' give '9999' twice, 'A-9999' and 'A9999' once."""
        self.isolate('res.partner')
        self.env['res.partner'].create([
            {'name': 'Zip %s' % zipcode, 'zip': zipcode, 'company_id': self.cx.id}
            for zipcode in ('1000', '1050', 'B-1000', 'B1000')])
        profile = self.run_profile(self.make_profile('res.partner', ['zip']))
        shapes = as_pairs(self.line(profile, 'zip').shapes)
        self.assertEqual(shapes, {'9999': 2, 'A-9999': 1, 'A9999': 1})

    def test_unusual_numbers_and_statistics(self):
        """SC-DGQ-0054-2: weights 4, 5, 5, 6, 5, 5000 show 1 unusual value, with min, max, mean and median."""
        self.isolate('product.template')
        for n, weight in enumerate((4, 5, 5, 6, 5, 5000)):
            self.env['product.template'].create(
                {'name': 'Weighty %s' % n, 'weight': weight, 'company_id': self.cx.id})
        profile = self.run_profile(self.make_profile('product.template', ['weight']))
        line = self.line(profile, 'weight')
        self.assertEqual(line.outlier_count, 1)
        self.assertEqual((line.min_value, line.max_value), (4.0, 5000.0))
        self.assertEqual(line.median_value, 5.0)
        self.assertEqual(round(line.mean_value, 1), 837.5)

    # ---- FR-DGQ-0055 -------------------------------------------------------

    def test_stale_records_and_unused_choices(self):
        """SC-DGQ-0055-1: 10 of 50 products not updated for over a year, and 'combo' unused among the types."""
        self.isolate('product.template')
        products = self.env['product.template'].create([
            {'name': 'Old %02d' % n, 'type': 'consu' if n % 2 else 'service', 'company_id': self.cx.id}
            for n in range(50)])
        for product in products[:10]:
            self.set_write_date(product, 400)
        profile = self.make_profile('product.template', ['type'])
        self.assertEqual(profile.stale_days, 365)
        self.run_profile(profile)
        self.assertEqual(profile.stale_count, 10)
        unused = dump(self.line(profile, 'type').unused_choices)
        self.assertIn('combo', unused)
        self.assertNotIn('service', unused)
        self.assertNotIn('consu', unused)

    def test_stale_days_can_be_changed(self):
        """FR-DGQ-0055: a record 400 days old is stale at 365 days and not at 500."""
        self.isolate('product.template')
        product = self.env['product.template'].create({'name': 'Aged', 'company_id': self.cx.id})
        self.set_write_date(product, 400)
        default = self.run_profile(self.make_profile('product.template', ['name']))
        longer = self.run_profile(self.make_profile('product.template', ['name'], stale_days=500))
        self.assertEqual(default.stale_count, 1)
        self.assertEqual(longer.stale_count, 0)
