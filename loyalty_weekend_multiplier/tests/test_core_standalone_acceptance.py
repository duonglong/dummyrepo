import glob
import os

from lxml import etree

from odoo.exceptions import ValidationError
from odoo.modules.module import get_manifest
from odoo.tests.common import TransactionCase

MODULE = 'loyalty_weekend_multiplier'
APPS = {'sale', 'sale_loyalty', 'point_of_sale', 'pos_loyalty'}


class TestCoreStandaloneAcceptance(TransactionCase):
    """Acceptance tests of T-010: the core module lives on Loyalty alone.

    Only base groups and loyalty records are used here, so the class runs
    on a database with neither Sales nor Point of Sale.
    """

    def _closure(self):
        closure, todo = set(), [MODULE]
        while todo:
            name = todo.pop()
            if name in closure:
                continue
            closure.add(name)
            manifest = get_manifest(name)
            self.assertTrue(manifest, f"Dependency {name} is not available")
            todo.extend(manifest.get('depends', []))
        return closure

    def _program(self, **vals):
        return self.env['loyalty.program'].create({
            'name': 'Core Program', 'program_type': 'loyalty', **vals})

    def test_manifest_depends_only_on_loyalty(self):
        """Requirements: FR-LAP-013 - SC-LAP-013-1: the core manifest depends only on loyalty."""
        self.assertEqual(get_manifest(MODULE)['depends'], ['loyalty'])

    def test_dependency_closure_has_no_sales_or_pos(self):
        """Requirements: FR-LAP-013 - SC-LAP-013-1: neither Sales nor Point of Sale is in the closure."""
        self.assertFalse(self._closure() & APPS, "Core pulls in Sales or Point of Sale")

    def test_closure_is_community(self):
        """Requirements: NFR-COMP-001 - the core closure holds no Enterprise module."""
        enterprise = os.environ.get('SDD_ENTERPRISE_SRC')
        enterprise = enterprise and os.path.realpath(enterprise)
        for name in self._closure():
            manifest = get_manifest(name)
            path = os.path.realpath(os.path.join(manifest['addons_path'], name))
            self.assertFalse(enterprise and path.startswith(enterprise + os.sep), name)
            self.assertNotEqual(manifest.get('license'), 'OEEL-1', name)

    def test_core_is_installed(self):
        """Requirements: FR-LAP-013 - SC-LAP-013-1: the installation succeeds."""
        module = self.env['ir.module.module'].search([('name', '=', MODULE)])
        self.assertEqual(module.state, 'installed')

    def test_multiplier_2_50_is_stored(self):
        """Requirements: FR-LAP-013 - SC-LAP-013-2: 2.50 is stored."""
        program = self._program(weekend_multiplier=2.5)
        self.env.flush_all()
        program.invalidate_recordset()
        self.assertEqual(program.weekend_multiplier, 2.5)

    def test_multiplier_zero_is_refused(self):
        """Requirements: FR-LAP-013 - SC-LAP-013-3: 0 is refused naming Weekend Multiplier."""
        program = self._program()
        with self.assertRaises(ValidationError) as ctx:
            program.weekend_multiplier = 0
            self.env.flush_all()
        self.assertIn('Weekend Multiplier', str(ctx.exception))

    def test_multiplier_just_below_zero_is_refused(self):
        """Requirements: FR-LAP-013 - SC-LAP-013-3 boundary: -0.01 is refused."""
        program = self._program()
        with self.assertRaises(ValidationError) as ctx:
            program.weekend_multiplier = -0.01
            self.env.flush_all()
        self.assertIn('Weekend Multiplier', str(ctx.exception))

    def test_multiplier_just_above_zero_is_accepted(self):
        """Requirements: FR-LAP-013 - SC-LAP-013-3 boundary: 0.01 is accepted."""
        program = self._program(weekend_multiplier=0.01)
        self.assertEqual(program.weekend_multiplier, 0.01)

    def test_gift_card_form_hides_the_field(self):
        """Requirements: FR-LAP-010 - SC-LAP-010-3: the form shows the field for Loyalty Cards only."""
        view = self.env['loyalty.program'].get_view(view_type='form')
        nodes = etree.fromstring(view['arch']).xpath("//field[@name='weekend_multiplier']")
        self.assertTrue(nodes, "Weekend Multiplier is missing on the program form")
        invisible = (nodes[0].get('invisible') or '').replace('"', "'").replace(' ', '')
        self.assertEqual(invisible, "program_type!='loyalty'")

    def test_core_tests_use_no_sales_or_pos_groups(self):
        """Requirements: NFR-MNT-002 - the core tests need no Sales or Point of Sale group."""
        tests_dir = os.path.dirname(os.path.abspath(__file__))
        offenders = []
        for path in glob.glob(os.path.join(tests_dir, '*.py')):
            if os.path.basename(path) == os.path.basename(__file__).replace('.pyc', '.py'):
                continue
            with open(path, encoding='utf-8') as handle:
                text = handle.read()
            if 'sales_team.group_' in text or 'point_of_sale.group_' in text:
                offenders.append(os.path.basename(path))
        self.assertFalse(offenders, f"Core tests need Sales or POS groups: {offenders}")
