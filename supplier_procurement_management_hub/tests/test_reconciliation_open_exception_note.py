"""Acceptance tests of T-037 (status line for open exceptions that do not hold posting).

Requirements: FR-SPM-111, FR-SPM-0077

The design names the computed field ``open_exception_note`` on
``procurement.reconciliation``. The matching page is a read-only form, so the
tests open it with Odoo's ``Form`` helper as a Finance reviewer and read the
field the page shows; the form step leaves to the build the rendering of the
line (an info block under the posting warning), checked here on the form the
server sends.
"""
from lxml import etree

from odoo.tests import Form, tagged

from .matching_common import SpmMatchingCommon

MODULE = 'supplier_procurement_management_hub'


@tagged('post_install', '-at_install')
class TestOpenExceptionNoteAcceptance(SpmMatchingCommon):
    """Requirements: FR-SPM-111"""

    def setUp(self):
        super().setUp()
        self._clear_config()
        self._policy('three_way')
        self._tolerance('price', percent=2.0, absolute=5.0)
        self._tolerance('quantity')

    def _page(self, bill):
        """The matching page of the bill, as the Finance reviewer opens it."""
        recon = self._recon(bill).with_user(self.user_fin)
        return Form(recon)

    def test_sc_111_01_two_open_exceptions_that_need_no_approval(self):
        """Requirements: FR-SPM-111. Scenario SC-SPM-111-01."""
        self._default_routes()
        bill = self._bill(self._order(receive=90), 100, 12.5)
        recon = self._recon(bill)
        self.assertEqual(sorted(recon.exception_ids.mapped('kind')), ['price', 'quantity'])
        recon.exception_ids.filtered(lambda e: e.kind == 'quantity').with_user(
            self.user_fin).action_spm_review()
        self.assertEqual(sorted(recon.exception_ids.mapped('state')), ['in_review', 'open'])
        page = self._page(bill)
        self.assertEqual(page.open_exception_note, '2 open exceptions, posting not held')
        self.assertFalse(page.posting_block_reason)

    def test_sc_111_01_one_open_exception_uses_the_singular(self):
        """Requirements: FR-SPM-111. Scenario SC-SPM-111-01, one exception."""
        self._default_routes()
        bill = self._bill(self._order(qty=100, receive=100), 100, 12.5)
        page = self._page(bill)
        self.assertEqual(page.open_exception_note, '1 open exception, posting not held')

    def test_sc_111_01_the_form_carries_the_line(self):
        """Requirements: FR-SPM-111. The page the server sends holds the line."""
        view = self.env['procurement.reconciliation'].with_user(self.user_fin).get_view(
            self.env.ref(f'{MODULE}.procurement_reconciliation_view_form').id, 'form')
        arch = etree.fromstring(view['arch'])
        self.assertTrue(arch.xpath("//field[@name='open_exception_note']"))

    def test_sc_111_02_exception_needing_approval_shows_the_warning_only(self):
        """Requirements: FR-SPM-111, FR-SPM-0077. Scenario SC-SPM-111-02."""
        self._default_routes(approval_kinds=('price',))
        bill = self._bill(self._order(qty=100, receive=100), 100, 12.5)
        page = self._page(bill)
        self.assertIn('Price', page.posting_block_reason)
        self.assertFalse(page.open_exception_note)

    def test_sc_111_03_an_accepted_exception_is_not_counted(self):
        """Requirements: FR-SPM-111. Scenario SC-SPM-111-03."""
        self._default_routes()
        bill = self._bill(self._order(qty=100, receive=100), 100, 12.5)
        exc = self._recon(bill).exception_ids
        exc.with_user(self.user_fin).action_spm_accept('Agreed surcharge')
        self.assertEqual(exc.state, 'accepted')
        page = self._page(bill)
        self.assertFalse(page.open_exception_note)
