"""Acceptance tests of T-021 (contracts: terms, lines, approval, revisions).

Requirements: FR-SPM-0062, FR-SPM-0063, FR-SPM-0064, FR-SPM-0065

Written from the specification. The save, approve and revise steps are
model methods (names in contract_common); the form screens are left to the build.
"""
import json
import re
from datetime import date

from freezegun import freeze_time

from odoo.exceptions import AccessError, UserError, ValidationError

from .contract_common import SpmContractCase


class TestContractAcceptance(SpmContractCase):

    # FR-SPM-0062 -----------------------------------------------------------
    def test_sc_0062_1_save_contract_is_draft_with_values(self):
        """Requirements: FR-SPM-0062. SC-SPM-0062-1: a manager saves a contract
        with Acme, 2027 dates, notice 60 days, EUR, scope category, ceiling 50,000."""
        contract = self._contract(
            start='2027-01-01', end='2027-12-31', notice=60, lines=[],
            reference='FW-2027', currency_id=self.eur.id,
            scope_kind='categories', product_category_ids=[(6, 0, self.furniture.ids)],
            ceiling_value=50000.0)
        self.assertEqual(contract.state, 'draft')
        self.assertEqual(contract.partner_id, self.acme)
        self.assertEqual(contract.owner_id, self.ann)
        self.assertEqual(contract.company_id, self.company)
        self.assertEqual(contract.date_start, date(2027, 1, 1))
        self.assertEqual(contract.date_end, date(2027, 12, 31))
        self.assertEqual(contract.notice_days, 60)
        self.assertEqual(contract.currency_id, self.eur)
        self.assertEqual(contract.scope_kind, 'categories')
        self.assertEqual(contract.product_category_ids, self.furniture)
        self.assertEqual(contract.ceiling_value, 50000.0)
        self.assertEqual(contract.reference, 'FW-2027')

    def test_sc_0062_2_end_before_start_is_refused(self):
        """Requirements: FR-SPM-0062. SC-SPM-0062-2: end 2026-12-31 before start 2027-01-01."""
        contract = self._contract(start='2027-01-01', end='2027-12-31')
        with self.assertRaises(ValidationError) as caught:
            contract.write({'date_end': '2026-12-31'})
            self.env.flush_all()
        self.assertIn('end date', str(caught.exception).lower())

    def test_fr_0062_end_equal_to_start_is_accepted(self):
        """Requirements: FR-SPM-0062. Boundary: end date equal to start date is allowed."""
        contract = self._contract(start='2027-01-01', end='2027-01-01')
        self.assertEqual(contract.date_end, contract.date_start)

    # FR-SPM-0063 -----------------------------------------------------------
    def test_sc_0063_1_line_holds_agreed_terms(self):
        """Requirements: FR-SPM-0063. SC-SPM-0063-1: chair 11.50 per unit, minimum 20,
        lead time 10 days, ceiling 1,000."""
        contract = self._contract(lines=[])
        line = self.env['procurement.contract.line'].with_user(self.ann).create(
            dict(self._line_vals(), contract_id=contract.id, discount=5.0))
        self.assertEqual(line.product_id, self.chair)
        self.assertEqual(line.price_unit, 11.5)
        self.assertEqual(line.uom_id, self.unit)
        self.assertEqual(line.discount, 5.0)
        self.assertEqual(line.min_qty, 20.0)
        self.assertEqual(line.lead_time_days, 10)
        self.assertEqual(line.ceiling_qty, 1000.0)

    def _create_line(self, **vals):
        contract = self._contract(lines=[])
        line = self.env['procurement.contract.line'].with_user(self.ann).create(
            dict(self._line_vals(), contract_id=contract.id, **vals))
        self.env.flush_all()
        return line

    def test_sc_0063_2_discount_above_100_is_refused(self):
        """Requirements: FR-SPM-0063. SC-SPM-0063-2: discount 120 percent."""
        with self.assertRaises(ValidationError):
            self._create_line(discount=120.0)

    def test_fr_0063_discount_100_is_accepted_and_just_above_refused(self):
        """Requirements: FR-SPM-0063. Boundary: 100 percent is allowed, 100.01 is not."""
        self.assertEqual(self._create_line(discount=100.0).discount, 100.0)
        with self.assertRaises(ValidationError):
            self._create_line(discount=100.01)

    def test_fr_0063_negative_price_is_refused(self):
        """Requirements: FR-SPM-0063. A negative price is refused, zero is allowed."""
        self.assertEqual(self._create_line(price_unit=0.0).price_unit, 0.0)
        with self.assertRaises(ValidationError):
            self._create_line(price_unit=-0.01)

    def test_fr_0063_negative_quantities_are_refused(self):
        """Requirements: FR-SPM-0063. A negative minimum or ceiling quantity is refused."""
        with self.assertRaises(ValidationError):
            self._create_line(min_qty=-1.0)
        with self.assertRaises(ValidationError):
            self._create_line(ceiling_qty=-1.0)

    # FR-SPM-0064 -----------------------------------------------------------
    def test_sc_0064_1_approved_then_active_on_start_date(self):
        """Requirements: FR-SPM-0064. SC-SPM-0064-1: written by Ann, starting 2027-01-01;
        Bob approves on 2026-12-15 (Approved); on 2027-01-01 the daily step makes it Active."""
        contract = self._contract(start='2027-01-01', end='2027-12-31')
        contract.with_user(self.ann).action_spm_submit()
        self.assertEqual(contract.state, 'to_approve')
        with freeze_time('2026-12-15 09:00:00'):
            contract.with_user(self.bob).action_spm_approve()
        self.assertEqual(contract.state, 'approved')
        self.assertEqual(contract.approved_by_id, self.bob)
        with freeze_time('2026-12-31 23:00:00'):
            self.env['procurement.contract']._spm_update_states()
        self.assertEqual(contract.state, 'approved')
        with freeze_time('2027-01-01 06:00:00'):
            self.env['procurement.contract']._spm_update_states()
        self.assertEqual(contract.state, 'active')

    def test_fr_0064_approval_after_start_date_is_active_at_once(self):
        """Requirements: FR-SPM-0064: start date already passed, approval makes it Active at once."""
        contract = self._contract(start='2026-06-01', end='2027-12-31')
        contract.with_user(self.ann).action_spm_submit()
        with freeze_time('2026-12-15 09:00:00'):
            contract.with_user(self.bob).action_spm_approve()
        self.assertEqual(contract.state, 'active')

    def test_sc_0064_2_author_cannot_approve_own_contract(self):
        """Requirements: FR-SPM-0064. SC-SPM-0064-2: Ann approves her own contract."""
        contract = self._contract(start='2027-01-01')
        contract.with_user(self.ann).action_spm_submit()
        with self.assertRaises(UserError) as caught:
            contract.with_user(self.ann).action_spm_approve()
        self.assertIn('another', str(caught.exception).lower())
        self.assertEqual(contract.state, 'to_approve')

    def test_fr_0064_administrator_may_approve_own_contract(self):
        """Requirements: FR-SPM-0064: an Administrator may approve a contract they wrote."""
        contract = self._contract(user=self.root, start='2027-01-01')
        contract.with_user(self.root).action_spm_submit()
        with freeze_time('2026-12-15 09:00:00'):
            contract.with_user(self.root).action_spm_approve()
        self.assertEqual(contract.state, 'approved')

    # FR-SPM-0065 -----------------------------------------------------------
    def test_sc_0065_1_revision_keeps_version_one_read_only(self):
        """Requirements: FR-SPM-0065. SC-SPM-0065-1: version 1 with chairs at 11.50;
        a revision to 11.00 approved on 2027-06-01."""
        contract = self._active_contract(lines=[self._line_vals(price=11.5)])
        self.assertEqual(contract.version, 1)
        contract.with_user(self.ann).action_spm_revise()
        self.assertEqual(contract.state, 'revision')
        contract.line_ids.with_user(self.ann).write({'price_unit': 11.0})
        contract.with_user(self.ann).action_spm_submit()
        with freeze_time('2027-06-01 09:00:00'):
            contract.with_user(self.bob).action_spm_approve()
        self.assertEqual(contract.state, 'active')
        self.assertEqual(contract.version, 2)
        self.assertEqual(contract.line_ids.price_unit, 11.0)
        version1 = contract.version_ids.filtered(lambda v: v.version == 1)
        self.assertEqual(len(version1), 1)
        self.assertEqual(version1.author_id, self.ann)
        self.assertIn('11.5', json.dumps(version1.terms))
        with self.assertRaises((UserError, AccessError)):
            version1.with_user(self.ann).write({'date_end': '2030-01-01'})
        diff = re.sub(r'<[^>]+>', ' ', version1.diff_html or '')
        self.assertRegex(diff, r'11\.5')
        self.assertRegex(diff, r'11\.0')
