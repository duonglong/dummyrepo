from .acceptance_m3_helpers import M3Case


class TestProfileShared(M3Case):
    """Regression tests for B-0219: a profile of a company also counts the records that belong to no company.

    The profile is filled in on its form, started and computed by the background runner, as an
    operator does. The profile screen and its exports are not reached.

    Requirements: FR-DGQ-0053, FR-DGQ-0054, FR-DGQ-0055, FR-DGQ-0071
    """

    def setUp(self):
        super().setUp()
        self.isolate('res.partner', 'product.template')

    def test_b_0219_shared_contacts_are_profiled_with_the_company_ones(self):
        """B-0219: 3 contacts of X and 2 shared ones, 1 of Y: the profile of X counts 5 and 2 empty e-mails."""
        Partner = self.env['res.partner']
        Partner.create(
            [{'name': 'In X %s' % n, 'email': 'x%s@example.com' % n, 'company_id': self.cx.id} for n in range(3)]
            + [{'name': 'Shared %s' % n, 'company_id': False} for n in range(2)]
            + [{'name': 'In Y', 'company_id': self.cy.id}])
        profile = self.run_profile(self.make_profile('res.partner', ['email']))
        line = self.line(profile, 'email')
        self.assertEqual((profile.record_count, line.empty_count), (5, 2))

    def test_b_0219_shared_products_are_profiled_with_the_company_ones(self):
        """B-0219: a product with no company counts in the profile of X; one of Y does not."""
        Product = self.env['product.template']
        Product.create([
            {'name': 'In X', 'company_id': self.cx.id},
            {'name': 'Shared', 'company_id': False},
            {'name': 'In Y', 'company_id': self.cy.id}])
        profile = self.run_profile(self.make_profile('product.template', ['name']))
        self.assertEqual(profile.record_count, 2)

    def test_b_0219_shared_records_count_in_the_stale_figure(self):
        """B-0219: the stale count of the profile reads the same source as the record count."""
        Partner = self.env['res.partner']
        shared = Partner.create({'name': 'Old shared', 'company_id': False})
        shared.flush_recordset()
        self.set_write_date(shared, 900)
        profile = self.run_profile(self.make_profile('res.partner', ['name'], stale_days=365))
        self.assertEqual((profile.record_count, profile.stale_count), (1, 1))
