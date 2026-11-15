"""Acceptance tests of T-028: sample rules and illustrative demo data.

The test database is built without demo data, so the demo half of SC-DGQ-0080-1 is checked on
the demo files the manifest names (every sample scan, issue and snapshot is marked illustrative)
and on the dashboard figures of an illustrative snapshot. NFR-DGQ-0007 and NFR-DGQ-0009 have no
Given/When/Then scenario and no test here.

Requirements: FR-DGQ-0080
"""
import ast
import os
from lxml import etree

from odoo import fields
from odoo.modules import get_module_path

from .acceptance_m3_helpers import M3Case

MODULE = 'data_governance_quality_center_app'
RESULT_MODELS = ('data.quality.scan', 'data.quality.issue', 'data.quality.score.snapshot')


class TestSampleDataAcceptance(M3Case):
    """Requirements: FR-DGQ-0080"""

    def sample_rules(self, model_name):
        return self.env['data.quality.rule'].with_user(self.manager_a).with_context(active_test=False).search(
            [('model_name', '=', model_name)])

    def test_sample_rules_exist_switched_off_without_demo_data(self):
        """SC-DGQ-0080-2: a manager finds sample rules for contacts, vendors and products, all switched off."""
        contacts = self.sample_rules('res.partner')
        products = self.sample_rules('product.template')
        self.assertTrue(contacts, 'no sample rule for contacts')
        self.assertTrue(products, 'no sample rule for products')
        self.assertTrue(
            contacts.filtered(lambda rule: 'vendor' in rule.name.lower() or 'supplier' in rule.name.lower()),
            'no sample rule for vendors (a rule named after vendors): %s' % contacts.mapped('name'))
        self.assertFalse((contacts | products).filtered('active'), 'sample rules are switched off')

    def test_no_scan_issue_or_snapshot_exists_without_demo_data(self):
        """SC-DGQ-0080-2: no scan, issue or snapshot exists after an install without demo data."""
        self.assertTrue(self.sample_rules('res.partner'), 'the sample rules exist')
        for name in RESULT_MODELS:
            self.assertEqual(self.env[name].sudo().with_context(active_test=False).search_count([]), 0, name)

    def test_sample_duplicate_rules_exist_for_contacts_and_products(self):
        """FR-DGQ-0080: ready duplicate rules for contacts and products come with the app."""
        Rule = self.env['data.quality.duplicate.rule'].sudo().with_context(active_test=False)
        models = set(Rule.search([]).mapped('model_name'))
        self.assertLessEqual({'res.partner', 'product.template'}, models)

    def test_demo_files_mark_every_sample_result_as_illustrative(self):
        """SC-DGQ-0080-1: the demo data holds sample scans, issues and snapshots, each marked illustrative."""
        with open(os.path.join(get_module_path(MODULE), '__manifest__.py')) as handle:
            demo_files = ast.literal_eval(handle.read()).get('demo', [])
        self.assertTrue(demo_files, 'the app has no demo file')
        seen = dict.fromkeys(RESULT_MODELS, 0)
        for relative in demo_files:
            tree = etree.parse(os.path.join(get_module_path(MODULE), relative))
            for record in tree.iter('record'):
                model = record.get('model')
                if model not in seen:
                    continue
                seen[model] += 1
                flag = [node for node in record.iter('field') if node.get('name') == 'is_illustrative']
                self.assertTrue(flag, '%s demo record %s lacks is_illustrative' % (model, record.get('id')))
                value = (flag[0].get('eval') or flag[0].text or '').strip()
                self.assertIn(value, ('True', 'true', '1'), '%s %s is not illustrative' % (model, record.get('id')))
        for model, count in seen.items():
            self.assertTrue(count, 'the demo data has no %s' % model)

    def snapshot(self, illustrative):
        return self.env['data.quality.score.snapshot'].sudo().create({
            'date': fields.Datetime.now(), 'company_id': self.cx.id, 'model_id': self.model('res.partner').id,
            'applicable_weight': 10, 'failed_weight': 1, 'score': 90.0, 'has_score': True,
            'weights_used': {'info': 1, 'warning': 3, 'critical': 10}, 'is_illustrative': illustrative})

    def figures(self):
        dashboard = self.env['data.quality.dashboard'].with_user(self.viewer).with_context(
            allowed_company_ids=self.cx.ids)
        return dashboard.get_figures(company_ids=[self.cx.id])

    def test_dashboard_figures_from_illustrative_snapshots_are_labelled(self):
        """SC-DGQ-0080-1: figures computed from an illustrative snapshot carry an illustrative flag."""
        self.snapshot(True)
        data = self.figures()
        flags = [value for key, value in data.items() if 'illustrative' in key]
        self.assertTrue(flags, 'no figure key says illustrative: %s' % sorted(data))
        self.assertTrue(all(flags))

    def test_dashboard_figures_from_live_snapshots_are_not_labelled(self):
        """FR-DGQ-0080: figures computed from a live snapshot are not labelled illustrative."""
        self.snapshot(False)
        data = self.figures()
        self.assertIn('illustrative', ' '.join(sorted(data)), 'the figures have no illustrative flag at all')
        self.assertFalse(any(value for key, value in data.items() if 'illustrative' in key))
