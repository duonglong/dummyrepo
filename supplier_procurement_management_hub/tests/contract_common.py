"""Shared fixtures of the contract acceptance tests (T-021, T-022).

Requirements: FR-SPM-0062, FR-SPM-0064

Names not fixed by the design are chosen here and recorded in the module notes:
- procurement.contract.action_spm_submit() (draft or revision -> to_approve),
  action_spm_approve() (to_approve -> approved, or active when the start date is
  reached; refused for the author unless Administrator), action_spm_revise()
  (active -> revision, snapshots version N), action_spm_renew(date_end),
  action_spm_update_vendor_prices() (returns the preview dialog action) and
  the daily step model._spm_update_states() (approved -> active at the start
  date, active -> expired after the end date).
- scope_kind values: products, categories, service.
- procurement.contract.price.update.action_confirm() applies the preview.
The form and dialog steps of the build are left out: the tests call the methods.
"""
from datetime import timedelta

from freezegun import freeze_time

from odoo.tests.common import TransactionCase

MODULE = 'supplier_procurement_management_hub'


class SpmContractCase(TransactionCase):

    def setUp(self):
        super().setUp()
        self.env = self.env(context=dict(self.env.context, tracking_disable=True))
        env = self.env
        self.company = env.company
        self.currency = self.company.currency_id
        self.eur = env.ref('base.EUR')
        self.eur.active = True
        partner_manager = env.ref('base.group_partner_manager').id

        def user(name, login, *xmlids):
            return env['res.users'].with_context(no_reset_password=True).create({
                'name': name, 'login': login, 'email': f'{login}@example.com',
                'company_id': self.company.id, 'company_ids': [(6, 0, self.company.ids)],
                'group_ids': [(6, 0, [env.ref(x).id for x in xmlids] + [partner_manager])],
            })
        self.ann = user('Ann Manager', 'spm_ann_m4', f'{MODULE}.group_spm_manager')
        self.bob = user('Bob Manager', 'spm_bob_m4', f'{MODULE}.group_spm_manager')
        self.root = user('Rita Admin', 'spm_rita_m4', f'{MODULE}.group_spm_manager', 'base.group_system')
        self.acme = env['res.partner'].create({'name': 'Acme M4', 'is_company': True})
        self.beta = env['res.partner'].create({'name': 'Beta M4', 'is_company': True})
        self.chair = env['product.product'].create({'name': 'Office chair M4', 'type': 'consu'})
        self.unit = env.ref('uom.product_uom_unit')
        self.furniture = env['product.category'].create({'name': 'Office furniture M4'})

    def _line_vals(self, price=11.5, min_qty=20.0, lead=10, ceiling=1000.0, product=None, **extra):
        return dict({
            'product_id': (product or self.chair).id, 'price_unit': price,
            'uom_id': self.unit.id, 'min_qty': min_qty, 'lead_time_days': lead,
            'ceiling_qty': ceiling,
        }, **extra)

    def _contract(self, user=None, partner=None, start='2026-06-01', end='2027-12-31',
                  notice=60, lines=None, **vals):
        """A draft contract written by ``user`` (Ann by default)."""
        user = user or self.ann
        lines = [self._line_vals()] if lines is None else lines
        return self.env['procurement.contract'].with_user(user).create(dict({
            'partner_id': (partner or self.acme).id,
            'owner_id': user.id,
            'company_id': self.company.id,
            'currency_id': self.currency.id,
            'date_start': start, 'date_end': end, 'notice_days': notice,
            'line_ids': [(0, 0, line) for line in lines],
        }, **vals))

    def _active_contract(self, approve_on=None, **kwargs):
        """A contract written by Ann, approved by Bob and Active.

        ``approve_on`` defaults to the day after the start date.
        """
        contract = self._contract(**kwargs)
        approve_on = approve_on or '%s 09:00:00' % (
            (contract.date_start + timedelta(days=1)).isoformat())
        contract.with_user(self.ann).action_spm_submit()
        with freeze_time(approve_on):
            contract.with_user(self.bob).action_spm_approve()
        self.assertEqual(contract.state, 'active', "helper: the contract must be Active")
        return contract
