"""Acceptance tests of T-018 (approval policies: conditions, approver
resolution, steps, cumulative rule), written from the specification.

Requirements: BR-SPM-0009, FR-SPM-0043, FR-SPM-0044, FR-SPM-0045, FR-SPM-0046

Names used (the design names none, fixed in tests/approval_common.py):
procurement.approval.policy.spm_evaluate(order) returns the matching policies,
the ordered steps and the cumulated amount. Submitting an order (the opening of
steps, the refusal of the submission, the cumulated amount on the request) is
built by T-019 and tested in tests/test_approval_flow_acceptance.py; here the
policies are evaluated. The policy form is not driven: its fields are plain.
"""
from freezegun import freeze_time

from odoo.exceptions import UserError

from .approval_common import SpmApprovalCase


class TestApprovalPolicyAcceptance(SpmApprovalCase):
    """Requirements: BR-SPM-0009, FR-SPM-0043, FR-SPM-0044, FR-SPM-0045, FR-SPM-0046"""

    def _evaluate(self, order):
        return self.env['procurement.approval.policy'].spm_evaluate(order)

    def _matches(self, policy, order):
        return policy in self._evaluate(order)['policies']

    def _bob_step(self):
        return self._step('Bob', 10, users=self.bob)

    def _foreign(self):
        foreign = self.env.ref('base.EUR') if self.company.currency_id != self.env.ref('base.EUR') \
            else self.env.ref('base.USD')
        foreign.active = True
        return foreign

    # FR-SPM-0043 -----------------------------------------------------------
    def test_sc_0043_1_amount_is_compared_in_the_company_currency(self):
        """SC-SPM-0043-1: a policy for this company, category IT, from 5,000;
        an IT order of 6,000 in a foreign currency with 1.10 of it per company
        currency unit is 5,454.55: the policy matches."""
        foreign = self._foreign()
        self.env['res.currency.rate'].create({
            'currency_id': foreign.id, 'company_id': self.company.id,
            'rate': 1.10, 'name': '2026-01-01'})
        policy = self._policy(
            'IT', steps=[self._bob_step()], amount_min=5000.0, product_category_ids=self.it_category)
        order = self._order(product=self.laptop, price=600.0, qty=10.0, currency=foreign,
                            date_order='2026-11-17 09:00:00')
        self.assertEqual(order.amount_total, 6000.0)
        self.assertTrue(self._matches(policy, order))

    def test_sc_0043_2_an_amount_below_the_threshold_does_not_match(self):
        """SC-SPM-0043-2: the same policy and an IT order of 4,999.99 in the
        company currency: the policy does not match."""
        policy = self._policy(
            'IT', steps=[self._bob_step()], amount_min=5000.0, product_category_ids=self.it_category)
        order = self._order(product=self.cable, price=4999.99, qty=1.0)
        self.assertFalse(self._matches(policy, order))

    def test_sc_0043_2_the_threshold_itself_matches(self):
        """FR-SPM-0043 boundary: an order of exactly 5,000.00 matches 'from 5,000'
        and one cent less does not."""
        policy = self._policy('IT', steps=[self._bob_step()], amount_min=5000.0)
        self.assertTrue(self._matches(policy, self._order(price=5000.0, qty=1.0)))
        self.assertFalse(self._matches(policy, self._order(price=4999.99, qty=1.0)))

    def test_sc_0043_2_an_order_above_the_upper_limit_does_not_match(self):
        """FR-SPM-0043: 'below a threshold' bounds the amount from above; a limit of
        0 means no upper limit."""
        limited = self._policy('Small', steps=[self._bob_step()], amount_min=1000.0, amount_max=8000.0)
        open_ended = self._policy('Any', 20, steps=[self._bob_step()], amount_min=1000.0, amount_max=0.0)
        order = self._order(price=9000.0, qty=1.0)
        self.assertFalse(self._matches(limited, order))
        self.assertTrue(self._matches(open_ended, order))
        self.assertTrue(self._matches(limited, self._order(price=2000.0, qty=1.0)))

    def test_sc_0043_2_every_condition_must_hold(self):
        """FR-SPM-0043: conditions combine with 'and': a policy for vendor Beta
        and category IT does not match an IT order of Acme, nor a chair order of
        Beta; a policy with no condition matches every order."""
        policy = self._policy(
            'Beta IT', steps=[self._bob_step()], partner_ids=self.beta,
            product_category_ids=self.it_category)
        everything = self._policy('All', 20, steps=[self._bob_step()])
        self.assertFalse(self._matches(policy, self._order(self.acme, product=self.laptop)))
        self.assertFalse(self._matches(policy, self._order(self.beta, product=self.chair)))
        self.assertTrue(self._matches(policy, self._order(self.beta, product=self.laptop)))
        self.assertTrue(self._matches(everything, self._order(self.acme, product=self.chair)))

    def test_sc_0043_2_other_companies_and_inactive_policies_do_not_apply(self):
        """FR-SPM-0043, BR-SPM-0009: only active policies of the order's company apply."""
        other = self.env['res.company'].create({'name': 'Other Co M3'})
        foreign_company = self._policy('Other', steps=[self._bob_step()], company_id=other.id)
        archived = self._policy('Archived', 20, steps=[self._bob_step()], active=False)
        order = self._order()
        matched = self._evaluate(order)['policies']
        self.assertNotIn(foreign_company, matched)
        self.assertNotIn(archived, matched)

    # FR-SPM-0044 -----------------------------------------------------------
    def test_sc_0044_1_manager_steps_follow_the_employee_hierarchy(self):
        """SC-SPM-0044-1: a manager step of 2 levels and requester Ann whose manager
        is Bob whose manager is Carla: the approvers are Bob then Carla; with 1 level
        only Bob."""
        self._employees()
        self._policy('Managers', steps=[self._step(
            'Chain', 10, approver_type='manager', manager_levels=2, mode='all')])
        order = self._order(requester=self.ann)
        steps = self._evaluate(order)['steps']
        self.assertEqual(len(steps), 1)
        self.assertEqual(set(steps[0]['approver_ids']), {self.bob.id, self.carla.id})
        self.assertEqual(steps[0]['approver_ids'][0], self.bob.id)

    def test_sc_0044_1_one_level_is_the_direct_manager_only(self):
        """FR-SPM-0044: a manager step of 1 level has the direct manager only."""
        self._employees()
        self._policy('Manager', steps=[self._step(
            'Direct', 10, approver_type='manager', manager_levels=1)])
        steps = self._evaluate(self._order(requester=self.ann))['steps']
        self.assertEqual(steps[0]['approver_ids'], [self.bob.id])

    def test_sc_0044_2_a_group_without_active_member_refuses_the_submission(self):
        """SC-SPM-0044-2: a group step 'Finance approvers' with no active member: the
        evaluation is refused with a message naming the step."""
        group = self.env['res.groups'].create({'name': 'Finance approvers M3'})
        ghost = self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'Gone', 'login': 'spm_gone_m3', 'group_ids': [(6, 0, [group.id, self.env.ref('base.group_user').id])]})
        ghost.active = False
        self._policy('Finance', steps=[self._step(
            'Finance approvers', 10, approver_type='group', group_id=group.id)])
        with self.assertRaises(UserError) as ctx:
            self._evaluate(self._order())
        self.assertIn('Finance approvers', str(ctx.exception))

    def test_sc_0044_2_a_group_step_resolves_to_its_active_members(self):
        """FR-SPM-0044: with an active member the group step is resolved to the
        active members only."""
        group = self.env['res.groups'].create({'name': 'Finance approvers M3b'})
        self.bob.group_ids = [(4, group.id)]
        ghost = self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'Gone', 'login': 'spm_gone2_m3', 'group_ids': [(6, 0, [group.id, self.env.ref('base.group_user').id])]})
        ghost.active = False
        self._policy('Finance', steps=[self._step(
            'Finance approvers', 10, approver_type='group', group_id=group.id)])
        steps = self._evaluate(self._order())['steps']
        self.assertEqual(steps[0]['approver_ids'], [self.bob.id])

    def test_sc_0044_2_a_step_of_inactive_users_is_refused(self):
        """FR-SPM-0044: a fixed-users step whose only user is inactive has no approver."""
        self.eve.active = False
        self._policy('Fixed', steps=[self._step('Eve only', 10, users=self.eve)])
        with self.assertRaises(UserError) as ctx:
            self._evaluate(self._order())
        self.assertIn('Eve only', str(ctx.exception))

    # BR-SPM-0009, FR-SPM-0045 ------------------------------------------------
    def test_sc_0045_1_steps_are_ordered_by_sequence(self):
        """SC-SPM-0045-1: steps IT (10, any), Security (10, any) and Finance (20,
        any): the evaluation gives the two steps of sequence 10 first, then Finance."""
        self._policy('Laptops', steps=[
            self._step('Finance', 20, users=self.eve),
            self._step('IT', 10, users=self.bob),
            self._step('Security', 10, users=self.carla)])
        steps = self._evaluate(self._order())['steps']
        self.assertEqual([s['sequence'] for s in steps], [10, 10, 20])
        self.assertEqual({s['name'] for s in steps[:2]}, {'IT', 'Security'})
        self.assertEqual(steps[2]['name'], 'Finance')
        self.assertEqual({s['mode'] for s in steps}, {'any'})

    def test_sc_0045_2_an_all_step_keeps_every_approver(self):
        """SC-SPM-0045-2: a step of mode all with approvers Bob and Carla is one
        step holding both, in mode all."""
        self._policy('Both', steps=[self._step(
            'Both', 10, users=self.bob | self.carla, mode='all')])
        steps = self._evaluate(self._order())['steps']
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0]['mode'], 'all')
        self.assertEqual(set(steps[0]['approver_ids']), {self.bob.id, self.carla.id})

    def test_br_0009_steps_follow_policy_sequence_whatever_the_creation_order(self):
        """BR-SPM-0009: the policy created first but of the higher sequence comes
        last: steps are ordered by policy sequence, then step sequence, so the same
        order data gives the same steps."""
        late = self._policy('Late', 20, steps=[self._step('Late step', 10, users=self.eve)])
        early = self._policy('Early', 5, steps=[self._step('Early step', 10, users=self.bob)])
        result = self._evaluate(self._order())
        self.assertEqual([s['name'] for s in result['steps']], ['Early step', 'Late step'])
        self.assertEqual([s['policy_id'] for s in result['steps']], [early.id, late.id])
        self.assertEqual(self._evaluate(self._order())['steps'], result['steps'])

    def test_br_0009_identical_approvers_of_one_sequence_are_asked_once(self):
        """BR-SPM-0009: two steps of the same sequence with the same approver
        merge into one step: Bob is asked once."""
        self._policy('Twice', steps=[
            self._step('IT', 10, users=self.bob), self._step('Security', 10, users=self.bob),
            self._step('Finance', 20, users=self.eve)])
        steps = self._evaluate(self._order())['steps']
        self.assertEqual(len(steps), 2)
        self.assertEqual(steps[0]['approver_ids'], [self.bob.id])
        self.assertEqual(steps[1]['approver_ids'], [self.eve.id])

    def test_br_0009_every_matching_policy_applies(self):
        """BR-SPM-0009: cumulative policies - a large IT order needs both the IT
        policy and the amount policy."""
        it = self._policy('IT', 10, steps=[self._step('IT', 10, users=self.bob)],
                          product_category_ids=self.it_category)
        amount = self._policy('Amount', 20, steps=[self._step('Amount', 10, users=self.eve)],
                              amount_min=5000.0)
        result = self._evaluate(self._order(product=self.laptop, price=6000.0, qty=1.0))
        self.assertEqual(result['policies'], it | amount)
        self.assertEqual(len(result['steps']), 2)

    # FR-SPM-0046 -----------------------------------------------------------
    def _confirmed(self, price, date_order):
        order = self._order(price=price, qty=1.0, date_order=date_order)
        order.button_confirm()
        self.assertEqual(order.state, 'purchase')
        return order

    def _cumulative_policy(self, period='month'):
        return self._policy(
            'Acme month', steps=[self._bob_step()], amount_min=10000.0,
            partner_ids=self.acme, cumulative_period=period)

    def test_sc_0046_1_the_cumulated_amount_triggers_the_policy(self):
        """SC-SPM-0046-1: a monthly cumulative policy from 10,000 for vendor Acme and
        8,000 of Acme orders confirmed this month: an Acme order of 3,000 matches
        and the cumulated amount is 11,000."""
        with freeze_time('2026-11-17 09:00:00'):
            policy = self._cumulative_policy()
            self._confirmed(8000.0, '2026-11-02 09:00:00')
            result = self._evaluate(self._order(price=3000.0, qty=1.0))
        self.assertIn(policy, result['policies'])
        self.assertAlmostEqual(result['cumulated_amount'], 11000.0, places=2)

    def test_sc_0046_1_orders_of_an_earlier_month_or_another_vendor_do_not_count(self):
        """FR-SPM-0046: an Acme order of the previous month and a Beta order of
        this month are not in the monthly sum: 3,000 alone stays below 10,000."""
        with freeze_time('2026-11-17 09:00:00'):
            policy = self._cumulative_policy()
            self._confirmed(8000.0, '2026-10-30 09:00:00')
            beta = self._order(self.beta, price=8000.0, qty=1.0, date_order='2026-11-03 09:00:00')
            beta.button_confirm()
            result = self._evaluate(self._order(price=3000.0, qty=1.0))
        self.assertNotIn(policy, result['policies'])

    def test_sc_0046_1_a_policy_without_period_ignores_earlier_orders(self):
        """FR-SPM-0046: only a policy with a cumulative period counts earlier
        orders: without one the 3,000 order stays below 10,000."""
        with freeze_time('2026-11-17 09:00:00'):
            policy = self._cumulative_policy(period='none')
            self._confirmed(8000.0, '2026-11-02 09:00:00')
            result = self._evaluate(self._order(price=3000.0, qty=1.0))
        self.assertNotIn(policy, result['policies'])

    def test_sc_0046_1_a_yearly_period_counts_the_whole_year(self):
        """FR-SPM-0046: with a yearly period an Acme order of March counts in November."""
        with freeze_time('2026-11-17 09:00:00'):
            policy = self._cumulative_policy(period='year')
            self._confirmed(8000.0, '2026-03-02 09:00:00')
            result = self._evaluate(self._order(price=3000.0, qty=1.0))
        self.assertIn(policy, result['policies'])
        self.assertAlmostEqual(result['cumulated_amount'], 11000.0, places=2)
