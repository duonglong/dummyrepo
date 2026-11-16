from .acceptance_helpers import AcceptanceCase


class TestRuleRequiredAcceptance(AcceptanceCase):
    """Acceptance tests of the required field check (T-029), seen through the scan result lines.

    The scan is created through the registry and run by the real background runner; the scan
    form step is left to the build.

    Requirements: FR-DGQ-0002
    """

    def test_missing_barcode_fails_with_barcode_explanation(self):
        """SC-DGQ-0002-1: 'Desk' has a reference and no barcode: it fails, explained by the barcode."""
        rule = self.make_rule('product.template', 'required', ['default_code', 'x_dgq_barcode'])
        desk = self.product('Desk', default_code='D-1')
        scan = self.run_scan([rule], models=['product.template'])
        failed = self.failing(scan, rule)
        self.assertIn(desk.id, failed)
        explanation = self.text(failed[desk.id])
        self.assertTrue('DGQ Barcode' in explanation or 'x_dgq_barcode' in explanation, explanation)

    def test_filled_fields_pass(self):
        """SC-DGQ-0002-1 (contrast): a product with both fields filled does not fail."""
        rule = self.make_rule('product.template', 'required', ['default_code', 'x_dgq_barcode'])
        full = self.product('Chair', default_code='C-1', x_dgq_barcode='123')
        scan = self.run_scan([rule], models=['product.template'])
        self.assertNotIn(full.id, self.failing(scan, rule))

    def test_spaces_only_email_fails(self):
        """SC-DGQ-0002-2: contact 'Acme' with an e-mail of three spaces fails the rule."""
        rule = self.make_rule('res.partner', 'required', ['email'])
        acme = self.partner('Acme', email='   ')
        good = self.partner('Beta', email='beta@example.com')
        scan = self.run_scan([rule], models=['res.partner'])
        failed = self.failing(scan, rule)
        self.assertIn(acme.id, failed)
        self.assertNotIn(good.id, failed)

    def test_sales_price_zero_passes(self):
        """SC-DGQ-0002-3: product 'Sample' with sales price 0 passes the rule."""
        rule = self.make_rule('product.template', 'required', ['list_price'])
        sample = self.product('Sample', list_price=0)
        scan = self.run_scan([rule], models=['product.template'])
        self.assertNotIn(sample.id, self.failing(scan, rule))
