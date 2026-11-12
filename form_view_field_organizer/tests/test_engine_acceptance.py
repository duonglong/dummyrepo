import importlib

from lxml import etree

from odoo.tests.common import TransactionCase

MODEL_FIELDS = {
    name: {'type': kind}
    for name, kind in (
        ('name', 'char'), ('phone', 'char'), ('vat', 'char'), ('bank', 'char'),
        ('country_id', 'many2one'), ('is_company', 'boolean'),
        ('regime', 'char'), ('is_blocked', 'boolean'), ('line_ids', 'one2many'),
    )
}


class TestEngineAcceptance(TransactionCase):
    """Acceptance tests of T-002: the pure layout engine, on literal arches.

    Requirements: BR-FVF-0001, BR-FVF-0005, BR-FVF-0006, FR-FVF-0002,
    FR-FVF-0004, FR-FVF-0005, FR-FVF-0006, FR-FVF-0007, FR-FVF-0015.
    Assumed call shapes (contract of T-002 leaves them open): arch_tree is an
    lxml element; model_fields maps a field name to {'type': <odoo type>};
    a spec line is {field, section_id, sequence, hide_refs, condition} where
    condition is a list of {field, operator, value} dicts, operator being one
    of is, is_not, set, not_set, gt, lt, ge, le and value a string.
    What needs the ORM (implied groups, saved data) is left to the build of
    the view hook task.
    """

    def _apply(self):
        return importlib.import_module('odoo.addons.form_view_field_organizer.engine.apply')

    def _expr(self):
        return importlib.import_module('odoo.addons.form_view_field_organizer.engine.expressions')

    def _tree(self, xml):
        return etree.fromstring(xml)

    def _spec(self, lines=(), sections=()):
        return {'layout_id': 1, 'sections': list(sections), 'lines': list(lines)}

    def _line(self, field, **kw):
        line = {'field': field, 'section_id': False, 'sequence': 10, 'hide_refs': [], 'condition': []}
        line.update(kw)
        return line

    def _section(self, sid, name, closed=False):
        return {'id': sid, 'name': name, 'start_closed': closed}

    def _apply_layout(self, xml, lines=(), sections=(), fields=None):
        tree = self._tree(xml)
        result = self._apply().apply_layout(
            tree, self._spec(lines, sections), MODEL_FIELDS if fields is None else fields)
        return result if result is not None else tree

    FORM = """<form><sheet>
        <group><field name="name"/><field name="phone"/><field name="vat"/><field name="bank"/></group>
    </sheet></form>"""

    # -- hide by group (FR-FVF-0002, BR-FVF-0005, BR-FVF-0001) ---------------

    def test_hide_every_occurrence_of_a_field(self):
        """SC-FVF-0002-1 (FR-FVF-0002): a field seen twice is hidden from the group at both places."""
        xml = """<form><sheet><group><field name="vat"/></group>
            <notebook><page string="Tax"><field name="vat"/></page></notebook></sheet></form>"""
        tree = self._apply_layout(xml, [self._line('vat', hide_refs=['base.group_user'])])
        found = tree.xpath('.//field[@name="vat"]')
        self.assertEqual(len(found), 2)
        for node in found:
            self.assertEqual(node.get('groups'), '!base.group_user')

    def test_hide_keeps_existing_groups(self):
        """BR-FVF-0001, BR-FVF-0006: a field already restricted to a group keeps that restriction and gains the negation."""
        xml = """<form><sheet><group><field name="vat" groups="base.group_system"/></group></sheet></form>"""
        tree = self._apply_layout(xml, [self._line('vat', hide_refs=['base.group_user'])])
        node = tree.xpath('.//field[@name="vat"]')[0]
        self.assertEqual(node.get('groups'), 'base.group_system,!base.group_user')

    def test_hide_from_two_groups(self):
        """FR-FVF-0002: hidden from several groups, each group is negated."""
        tree = self._apply_layout(
            self.FORM, [self._line('vat', hide_refs=['base.group_user', 'base.group_portal'])])
        groups = tree.xpath('.//field[@name="vat"]')[0].get('groups').split(',')
        self.assertIn('!base.group_user', groups)
        self.assertIn('!base.group_portal', groups)

    def test_hide_leaves_other_fields_alone(self):
        """FR-FVF-0002: users outside the group, and other fields, are unaffected."""
        tree = self._apply_layout(self.FORM, [self._line('vat', hide_refs=['base.group_user'])])
        self.assertIsNone(tree.xpath('.//field[@name="phone"]')[0].get('groups'))

    def test_hide_does_not_touch_subview_fields(self):
        """BR-FVF-0005: fields inside an x2many sub-view are left as they are."""
        xml = """<form><sheet><group><field name="phone"/></group>
            <field name="line_ids"><list><field name="phone"/></list></field></sheet></form>"""
        tree = self._apply_layout(xml, [self._line('phone', hide_refs=['base.group_user'])])
        inner = tree.xpath('.//field[@name="line_ids"]//field[@name="phone"]')[0]
        self.assertIsNone(inner.get('groups'))
        outer = tree.xpath('.//group/field[@name="phone"]')[0]
        self.assertEqual(outer.get('groups'), '!base.group_user')

    # -- conditions (FR-FVF-0005) ---------------------------------------------

    def test_build_condition_joins_lines_with_and(self):
        """SC-FVF-0005-2 (FR-FVF-0005): all condition lines must hold, joined with and."""
        expr = self._expr().build_condition(
            [{'field': 'country_id', 'operator': 'is', 'value': '21'},
             {'field': 'is_company', 'operator': 'set', 'value': ''}],
            {'country_id': 'many2one', 'is_company': 'boolean'})
        self.assertEqual(expr, 'country_id == 21 and is_company')

    def test_build_condition_text_value_is_a_literal(self):
        """FR-FVF-0005: a text value becomes a quoted Python literal, not typed text."""
        expr = self._expr().build_condition(
            [{'field': 'name', 'operator': 'is', 'value': 'Acme'}], {'name': 'char'})
        self.assertEqual(expr, "name == 'Acme'")

    def test_build_condition_number_comparisons(self):
        """FR-FVF-0005: at least / at most / greater / less on numbers."""
        build = self._expr().build_condition
        types = {'qty': 'integer'}
        for op, sign in (('gt', '>'), ('lt', '<'), ('ge', '>='), ('le', '<=')):
            self.assertEqual(
                build([{'field': 'qty', 'operator': op, 'value': '5'}], types), 'qty %s 5' % sign)

    def test_build_condition_refuses_order_comparison_on_date(self):
        """FR-FVF-0005 (decision table): dates offer only is set / is not set."""
        build = self._expr().build_condition
        with self.assertRaises(Exception):
            build([{'field': 'birthday', 'operator': 'gt', 'value': '2020-01-01'}], {'birthday': 'date'})

    def test_build_condition_refuses_order_comparison_on_text(self):
        """FR-FVF-0005 (decision table): greater/less are for numbers and amounts only."""
        build = self._expr().build_condition
        with self.assertRaises(Exception):
            build([{'field': 'name', 'operator': 'lt', 'value': 'b'}], {'name': 'char'})

    def test_combine_with_original_invisible(self):
        """BR-FVF-0001: the condition is ORed onto the original invisible, so it never reveals."""
        self.assertEqual(
            self._expr().combine('is_blocked', 'country_id == 21 and is_company'),
            '(is_blocked) or not (country_id == 21 and is_company)')

    def test_condition_rewrites_invisible(self):
        """SC-FVF-0005-1/2 (FR-FVF-0005): the field is hidden unless every line holds."""
        xml = """<form><sheet><group><field name="regime" invisible="is_blocked"/>
            <field name="country_id"/><field name="is_company"/><field name="is_blocked"/></group></sheet></form>"""
        cond = [{'field': 'country_id', 'operator': 'is', 'value': '21'},
                {'field': 'is_company', 'operator': 'set', 'value': ''}]
        tree = self._apply_layout(xml, [self._line('regime', condition=cond)])
        node = tree.xpath('.//field[@name="regime"]')[0]
        self.assertEqual(node.get('invisible'), '(is_blocked) or not (country_id == 21 and is_company)')

    def test_condition_on_missing_field_is_skipped(self):
        """SC-FVF-0015-1 (FR-FVF-0015): a condition on a vanished field is dropped, the form still builds."""
        cond = [{'field': 'ghost_id', 'operator': 'is', 'value': '1'}]
        tree = self._apply_layout(self.FORM, [self._line('vat', condition=cond)])
        node = tree.xpath('.//field[@name="vat"]')[0]
        self.assertFalse(node.get('invisible'))

    # -- sections (FR-FVF-0004, FR-FVF-0007, FR-FVF-0006) ---------------------

    def _sections(self, tree):
        return tree.xpath('.//div[contains(concat(" ", normalize-space(@class), " "), " o_fvf_section ")]')

    def test_section_start_state(self):
        """SC-FVF-0004-1 (FR-FVF-0004): a closed section carries start_closed 1, an open one 0."""
        lines = [self._line('phone', section_id=1), self._line('vat', section_id=2)]
        tree = self._apply_layout(
            self.FORM, lines,
            [self._section(1, 'Contact details', False), self._section(2, 'Accounting', True)])
        by_name = {s.get('string'): s for s in self._sections(tree)}
        self.assertEqual(by_name['Accounting'].get('start_closed'), '1')
        self.assertEqual(by_name['Contact details'].get('start_closed'), '0')
        self.assertEqual(by_name['Accounting'].get('name'), 'fvf_section_2')

    def test_sections_follow_original_content_in_order(self):
        """FR-FVF-0004: sections are the last children of the sheet, in the given order."""
        lines = [self._line('phone', section_id=1), self._line('vat', section_id=2)]
        tree = self._apply_layout(
            self.FORM, lines, [self._section(1, 'First'), self._section(2, 'Second')])
        sheet = tree.xpath('.//sheet')[0]
        children = list(sheet)
        self.assertEqual([c.get('string') for c in children[-2:]], ['First', 'Second'])
        self.assertEqual(children[0].tag, 'group')

    def test_move_field_into_section_once(self):
        """SC-FVF-0007-1 (FR-FVF-0007): the field shows once, in its new section only."""
        lines = [self._line('phone', section_id=2)]
        tree = self._apply_layout(
            self.FORM, lines, [self._section(1, 'Contact details'), self._section(2, 'Accounting')])
        found = tree.xpath('.//field[@name="phone"]')
        self.assertEqual(len(found), 1)
        self.assertTrue(found[0].xpath('ancestor::div[@name="fvf_section_2"]'))

    def test_move_takes_every_occurrence_and_label(self):
        """FR-FVF-0007: all occurrences and the label of the field leave their old place."""
        xml = """<form><sheet><group><label for="phone"/><field name="phone"/><field name="vat"/></group>
            <notebook><page string="More"><field name="phone"/></page></notebook></sheet></form>"""
        tree = self._apply_layout(xml, [self._line('phone', section_id=1)], [self._section(1, 'S')])
        self.assertEqual(len(tree.xpath('.//field[@name="phone"]')), 1)
        for label in tree.xpath('.//label[@for="phone"]'):
            self.assertTrue(label.xpath('ancestor::div[@name="fvf_section_1"]'))

    def test_move_out_of_restricted_page_keeps_restriction(self):
        """BR-FVF-0001: a field moved out of a groups-restricted page stays restricted to those groups."""
        xml = """<form><sheet><group><field name="vat"/></group>
            <notebook><page string="Admin" groups="base.group_system"><field name="phone"/></page></notebook>
            </sheet></form>"""
        tree = self._apply_layout(xml, [self._line('phone', section_id=1)], [self._section(1, 'S')])
        node = tree.xpath('.//field[@name="phone"]')[0]
        self.assertTrue(node.xpath('ancestor-or-self::*[@groups="base.group_system"]'))
        self.assertTrue(node.xpath('ancestor::div[@name="fvf_section_1"]'))

    def test_order_inside_a_section(self):
        """SC-FVF-0006-1 (FR-FVF-0006): fields show in the sequence chosen inside the section."""
        lines = [self._line('vat', section_id=1, sequence=20), self._line('bank', section_id=1, sequence=10)]
        tree = self._apply_layout(self.FORM, lines, [self._section(1, 'Accounting')])
        section = tree.xpath('.//div[@name="fvf_section_1"]')[0]
        self.assertEqual([f.get('name') for f in section.xpath('.//field')], ['bank', 'vat'])

    def test_unplaced_fields_keep_their_place(self):
        """SC-FVF-0006-1 (FR-FVF-0006): fields not placed in a section stay where they were."""
        lines = [self._line('vat', section_id=1), self._line('bank', section_id=1)]
        tree = self._apply_layout(self.FORM, lines, [self._section(1, 'Accounting')])
        group = tree.xpath('.//sheet/group')[0]
        self.assertEqual([f.get('name') for f in group.xpath('./field')], ['name', 'phone'])

    # -- missing fields (FR-FVF-0015) -------------------------------------------

    def test_line_for_missing_field_is_skipped(self):
        """SC-FVF-0015-1 (FR-FVF-0015): a line naming a vanished field raises nothing and adds nothing."""
        lines = [self._line('loyalty_card', section_id=1, hide_refs=['base.group_user']),
                 self._line('phone', section_id=1)]
        tree = self._apply_layout(self.FORM, lines, [self._section(1, 'Sales')])
        self.assertFalse(tree.xpath('.//field[@name="loyalty_card"]'))
        self.assertTrue(tree.xpath('.//div[@name="fvf_section_1"]//field[@name="phone"]'))

    def test_line_for_field_not_in_the_form_is_skipped(self):
        """FR-FVF-0015: a model field absent from the arch is not inserted either."""
        tree = self._apply_layout(
            self.FORM, [self._line('regime', section_id=1)], [self._section(1, 'Sales')])
        self.assertFalse(tree.xpath('.//field[@name="regime"]'))

    # -- pruning (FR-FVF-0015, FR-FVF-0004) ----------------------------------------

    def test_prune_drops_section_without_field(self):
        """FR-FVF-0015: a section left with no field is dropped from the form."""
        tree = self._apply_layout(
            self.FORM, [self._line('loyalty_card', section_id=1)], [self._section(1, 'Sales')])
        pruned = self._apply().prune_empty_sections(tree)
        tree = pruned if pruned is not None else tree
        self.assertFalse(self._sections(tree))

    def test_prune_keeps_section_with_a_field(self):
        """FR-FVF-0004: a section holding a field survives pruning."""
        tree = self._apply_layout(
            self.FORM, [self._line('phone', section_id=1)], [self._section(1, 'Contact details')])
        pruned = self._apply().prune_empty_sections(tree)
        tree = pruned if pruned is not None else tree
        self.assertEqual(len(self._sections(tree)), 1)

    def test_prune_hides_section_whose_fields_are_all_invisible(self):
        """FR-FVF-0005: a section whose fields are all invisible is invisible itself (their AND)."""
        xml = """<form><sheet><group><field name="phone" invisible="is_blocked"/>
            <field name="vat" invisible="is_company"/></group></sheet></form>"""
        tree = self._apply_layout(
            xml, [self._line('phone', section_id=1), self._line('vat', section_id=1)],
            [self._section(1, 'S')])
        pruned = self._apply().prune_empty_sections(tree)
        tree = pruned if pruned is not None else tree
        invisible = self._sections(tree)[0].get('invisible') or ''
        self.assertIn('is_blocked', invisible)
        self.assertIn('is_company', invisible)
        self.assertIn(' and ', invisible)
