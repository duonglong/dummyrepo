"""Multi-company: every company-owned model of the app is cut by company, records and reports alike.

Requirements: NFR-SPM-0008
"""
import re

from odoo.tests.common import TransactionCase

MODULE = 'supplier_procurement_management_hub'


class TestMultiCompany(TransactionCase):
    """Requirements: NFR-SPM-0008"""

    def setUp(self):
        super().setUp()
        self.env = self.env(context=dict(self.env.context, tracking_disable=True))
        self.company_a = self.env.company
        self.company_b = self.env['res.company'].create({'name': 'Company B NFR'})
        group = self.env.ref(f'{MODULE}.group_spm_manager')
        self.only_b = self._user('only_b', group, self.company_b)
        self.both = self._user('both', group, self.company_a | self.company_b)

    def _user(self, login, group, companies):
        return self.env['res.users'].with_context(no_reset_password=True).create({
            'name': f'User {login}', 'login': f'spm_nfr_{login}', 'company_id': companies[0].id,
            'company_ids': [(6, 0, companies.ids)], 'group_ids': [(6, 0, [group.id])]})

    def _own_models(self):
        """The models of the app that hold a stored company field, with the accepted company paths of their rule.

        A model with no company of its own but a required parent that has one (a cascade child) is owned
        through that parent, so it is returned with the parent's field path, e.g. ``application_id.company_id``.
        """
        own = {}
        models = [model for name, model in self.env.registry.items()
                  if model._original_module == MODULE and not model._abstract and not model._transient]
        for model in models:
            field = model._fields.get('company_id')
            if field and field.store:
                own[model._name] = ['company_id']
                if field.related:
                    own[model._name].append(field.related if isinstance(field.related, str) else '.'.join(field.related))
        for model in models:
            if model._name in own:
                continue
            for field in model._fields.values():
                if (field.type == 'many2one' and field.required and field.store
                        and field.comodel_name in own and field.ondelete == 'cascade'):
                    own[model._name] = [f'{field.name}.company_id']
                    break
        return own

    def test_nfr_0008_every_company_owned_model_has_a_company_rule(self):
        """NFR-SPM-0008: a rule for all users (no group) limits each company-owned model, and each cascade child
        of one through its parent, to exactly the allowed companies."""
        accesses = self.env['ir.access'].sudo().search([('group_id', '=', False)])  # sudo: test reads the rules
        missing = []
        for name, paths in self._own_models().items():
            domains = accesses.filtered(lambda a: a.model_id.model == name).mapped('domain')
            if not any(re.search(rf"\(\s*'(?:{'|'.join(map(re.escape, paths))})'\s*,\s*'in'\s*,\s*company_ids(\s*\+\s*\[False\])?\s*\)",
                                 domain or '') for domain in domains):
                missing.append(name)
        self.assertEqual(missing, [], 'company-owned models without a company rule')

    def test_nfr_0008_contacts_of_an_application_follow_its_company(self):
        """NFR-SPM-0008: a user of company B alone finds no contact of a company-A application."""
        Application = self.env['procurement.supplier.application']
        application = Application.create({
            'legal_name': 'Applicant A', 'email': 'a@example.com', 'company_id': self.company_a.id,
            'supplier_type_id': self.env['procurement.supplier.type'].create({'name': 'Goods NFR8'}).id})
        contact = self.env['procurement.supplier.application.contact'].create(
            {'application_id': application.id, 'name': 'Person A'})
        Contact = self.env['procurement.supplier.application.contact']
        self.assertEqual(Contact.with_user(self.only_b).search([('id', '=', contact.id)]), Contact)
        both = Contact.with_user(self.both).with_context(
            allowed_company_ids=(self.company_a | self.company_b).ids)
        self.assertEqual(both.search([('id', '=', contact.id)]), contact)

    def test_nfr_0008_a_user_of_one_company_sees_nothing_of_the_other(self):
        """NFR-SPM-0008: two policies of two companies: the user of company B alone reads B's, the user of both reads both."""
        Policy = self.env['procurement.approval.policy']
        in_a = Policy.create({'name': 'Policy A', 'company_id': self.company_a.id})
        in_b = Policy.create({'name': 'Policy B', 'company_id': self.company_b.id})
        domain = [('id', 'in', (in_a | in_b).ids)]
        self.assertEqual(Policy.with_user(self.only_b).search(domain), in_b)
        both = Policy.with_user(self.both).with_context(
            allowed_company_ids=(self.company_a | self.company_b).ids)
        self.assertEqual(both.search(domain), in_a | in_b)

    def test_nfr_0008_reports_are_cut_by_company_too(self):
        """NFR-SPM-0008: the report views carry the company and the company rule, so an export holds one company's rows."""
        accesses = self.env['ir.access'].sudo().search([('group_id', '=', False)])  # sudo: test reads the rules
        reports = [name for name, model in self.env.registry.items()
                   if model._original_module == MODULE and name.startswith('procurement.report.')
                   and 'company_id' in model._fields and not model._abstract]
        self.assertTrue(reports)
        for name in reports:
            domains = accesses.filtered(lambda a: a.model_id.model == name).mapped('domain')
            self.assertTrue(any('company' in (domain or '') for domain in domains), name)
            self.assertEqual(self.env[name].with_user(self.only_b).search_count(
                [('company_id', '=', self.company_a.id)]), 0, name)
