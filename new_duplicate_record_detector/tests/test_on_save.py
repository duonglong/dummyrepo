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
