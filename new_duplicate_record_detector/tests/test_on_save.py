import logging
from unittest.mock import patch

from odoo import Command
from odoo.tests.common import TransactionCase


class TestOnSave(TransactionCase):
    """Tests for FR-DRD-0003, FR-DRD-0004, NFR-DRD-0001."""

    def setUp(self):
        super().setUp()
        self.rule = self.env['duplicate.rule'].create({
            'name': 'T002 e-mail rule',
            'model_id': self.env['ir.model']._get('res.partner').id,
            'condition_ids': [Command.create({
                'field_id': self.env['ir.model.fields']._get('res.partner', 'email').id})],
        })

    def test_matcher_error_does_not_block_save(self):
        """Requirements: FR-DRD-0003. A failing check is logged and the record is still saved."""
        self.env['res.partner'].create({'name': 'Before', 'email': 'e@err-t002.com'})
        matcher = type(self.env['duplicate.matcher'])
        with patch.object(matcher, 'find_matches', side_effect=ValueError('boom')), \
                self.assertLogs('odoo.addons.new_duplicate_record_detector', logging.ERROR):
            partner = self.env['res.partner'].create({'name': 'After', 'email': 'e@err-t002.com'})
        self.assertTrue(partner.exists())
        self.assertFalse(self.env['duplicate.group'].search([('rule_id', '=', self.rule.id)]))

    def test_write_of_unrelated_field_does_not_check(self):
        """Requirements: FR-DRD-0004. A write that touches no rule field runs no matching."""
        partner = self.env['res.partner'].create({'name': 'Quiet', 'email': 'q@quiet-t002.com'})
        matcher = type(self.env['duplicate.matcher'])
        with patch.object(matcher, 'find_matches', side_effect=AssertionError) as find:
            partner.write({'city': 'Lyon'})
        find.assert_not_called()

    def test_save_cost_with_three_exact_rules(self):
        """Requirements: NFR-DRD-0001. Three exact rules cost a bounded number of queries per save."""
        fields = self.env['ir.model.fields']
        for name in ('city', 'street'):
            self.env['duplicate.rule'].create({
                'name': f'T002 {name}',
                'model_id': self.env['ir.model']._get('res.partner').id,
                'condition_ids': [Command.create({'field_id': fields._get('res.partner', name).id})],
            })
        self.env['res.partner'].create({
            'name': 'Cost', 'email': 'c@cost-t002.com', 'city': 'Nice', 'street': 'Rue 1'})
        self.env.flush_all()
        self.env.invalidate_all()
        with self.assertQueryCount(__system__=25):
            self.env['res.partner'].create({
                'name': 'Cost2', 'email': 'd@cost-t002.com', 'city': 'Nice2', 'street': 'Rue 2'})

    def test_warning_names_only_records_the_saver_can_read(self):
        """Requirements: FR-DRD-0003, BR-DRD-0004. A shared record's warning hides other companies' records."""
        company_a = self.env.company
        company_b = self.env['res.company'].create({'name': 'T002 Company B'})
        saver = self.env['res.users'].create({
            'name': 'T002 saver', 'login': 't002-saver',
            'company_id': company_a.id, 'company_ids': [Command.set(company_a.ids)],
            'group_ids': [Command.set([
                self.env.ref('base.group_user').id, self.env.ref('base.group_partner_manager').id])],
        })
        self.env['res.partner'].create({
            'name': 'Hidden Of B', 'email': 'x@scope-t002.com', 'company_id': company_b.id})
        mark = self.env['bus.bus'].sudo().search([], order='id desc', limit=1).id or 0
        self.env['res.partner'].with_user(saver).create({'name': 'Shared', 'email': 'x@scope-t002.com'})
        messages = self.env['bus.bus'].sudo().search([('id', '>', mark)]).mapped('message')
        self.assertFalse([m for m in messages if 'Hidden Of B' in m])

    def test_bug_b_0147_company_and_its_own_contact_are_not_matches(self):
        """Regression test for B-0147: saving a contact with its company's e-mail records no set.

        Requirements: FR-DRD-0004, BR-DRD-0004
        """
        company = self.env['res.partner'].create({
            'name': 'Acme B0147', 'is_company': True, 'email': 'info@b0147.com'})
        contact = self.env['res.partner'].create({
            'name': 'John B0147', 'parent_id': company.id, 'email': 'info@b0147.com'})
        matches = self.env['duplicate.matcher'].find_matches(contact, self.rule)
        self.assertFalse(matches)
        self.assertFalse(self.env['duplicate.group'].search([('rule_id', '=', self.rule.id)]))
        self.assertFalse(self.env['duplicate.matcher'].find_matches(company, self.rule))

    def test_bug_b_0148_outside_company_does_not_bring_a_company_and_its_contact_together(self):
        """Regression test for B-0148: saving an outside company with the e-mail of a company and
        of its contact never puts the company and that contact in one set.

        Requirements: FR-DRD-0004, BR-DRD-0004
        """
        company = self.env['res.partner'].create({
            'name': 'Acme B0148', 'is_company': True, 'email': 'info@b0148.com'})
        contact = self.env['res.partner'].create({
            'name': 'John B0148', 'parent_id': company.id, 'email': 'info@b0148.com'})
        outside = self.env['res.partner'].create({
            'name': 'ACME Ltd B0148', 'is_company': True, 'email': 'info@b0148.com'})
        self.env.flush_all()
        sets = [set(g.line_ids.mapped('res_id')) for g in
                self.env['duplicate.group'].search([('rule_id', '=', self.rule.id)])]
        self.assertTrue(sets)
        for members in sets:
            self.assertFalse({company.id, contact.id} <= members)
            self.assertIn(outside.id, members)

    def test_bug_b_0149_save_linking_two_open_sets_keeps_company_and_contact_apart(self):
        """Regression test for B-0149: a saved record that matches a member of the set of a company
        and a member of the set of that company's own contact never joins the two sets.

        Requirements: FR-DRD-0004, BR-DRD-0004, BR-DRD-0005
        """
        rule = self.env['duplicate.rule'].create({
            'name': 'B0149 partial name',
            'model_id': self.env['ir.model']._get('res.partner').id,
            'condition_ids': [Command.create({
                'field_id': self.env['ir.model.fields']._get('res.partner', 'name').id,
                'match_type': 'partial'})],
        })
        partner = self.env['res.partner']
        company = partner.create({'name': 'Acmeq', 'is_company': True})
        outside_company = partner.create({'name': 'Acmeq Tradingz', 'is_company': True})
        contact = partner.create({'name': 'Jonq Smithq', 'parent_id': company.id})
        outside_contact = partner.create({'name': 'Jonq Smithq Tradingz', 'is_company': True})
        partner.create({'name': 'Tradingz'})
        self.env.flush_all()
        sets = [set(g.line_ids.mapped('res_id')) for g in
                self.env['duplicate.group'].search([('rule_id', '=', rule.id), ('state', '=', 'open')])]
        self.assertTrue(any({outside_company.id, outside_contact.id} <= s for s in sets))
        self.assertFalse(any({company.id, contact.id} <= s for s in sets))
