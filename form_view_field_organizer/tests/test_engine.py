from lxml import etree

from odoo.tests.common import TransactionCase

from odoo.addons.form_view_field_organizer.engine import apply

FIELDS = {name: {'type': 'char'} for name in ('a', 'b', 'c')}


class TestEngine(TransactionCase):
    """Unit tests of the engine beyond the acceptance tests.

    Requirements: FR-FVF-0002, FR-FVF-0007, BR-FVF-0001
    """

    def _line(self, field, **kw):
        line = {'field': field, 'section_id': 1, 'sequence': 10, 'hide_refs': [], 'condition': []}
        line.update(kw)
        return line

    def _run(self, xml, lines):
        spec = {'layout_id': 1, 'sections': [{'id': 1, 'name': 'S', 'start_closed': False}], 'lines': lines}
        return apply.apply_layout(etree.fromstring(xml), spec, FIELDS)

    def test_moved_field_inherits_former_container_invisible(self):
        """BR-FVF-0001: the invisible rule of the old container follows the moved field."""
        tree = self._run(
            '<form><sheet><group invisible="c"><field name="a"/></group></sheet></form>', [self._line('a')])
        self.assertEqual(tree.xpath('.//field[@name="a"]')[0].get('invisible'), 'c')

    def test_moved_field_wrapped_per_former_groups(self):
        """BR-FVF-0001: one div per former container groups value, outermost first."""
        tree = self._run(
            '<form><sheet><group groups="x.g1"><group groups="x.g2"><field name="a"/></group></group>'
            '</sheet></form>', [self._line('a')])
        node = tree.xpath('.//field[@name="a"]')[0]
        self.assertEqual([d.get('groups') for d in reversed(list(node.iterancestors('div')))][1:],
                         ['x.g1', 'x.g2'])

    def test_emptied_wrapper_div_is_removed(self):
        """FR-FVF-0007: a div left empty by the move disappears."""
        tree = self._run(
            '<form><sheet><div name="w"><field name="a"/></div><field name="b"/></sheet></form>',
            [self._line('a')])
        self.assertFalse(tree.xpath('.//div[@name="w"]'))

    def test_form_without_sheet_puts_section_before_chatter(self):
        """FR-FVF-0004: without a sheet the section goes before the chatter."""
        tree = self._run('<form><field name="a"/><chatter/></form>', [self._line('a')])
        self.assertEqual([c.tag for c in tree], ['div', 'chatter'])

    def test_twice_seen_field_shows_wherever_any_occurrence_showed(self):
        """FR-FVF-0007 (standing answer): invisible rules of two occurrences are ANDed."""
        tree = self._run(
            '<form><sheet><group invisible="c"><field name="a"/></group>'
            '<group invisible="not c"><field name="a"/></group></sheet></form>', [self._line('a')])
        node = tree.xpath('.//field[@name="a"]')[0]
        self.assertEqual(len(tree.xpath('.//field[@name="a"]')), 1)
        self.assertEqual(node.get('invisible'), '(c) and (not c)')

    def test_twice_seen_field_unrestricted_once_has_no_rule(self):
        """FR-FVF-0007: one unrestricted occurrence means the moved field has no invisible rule."""
        tree = self._run(
            '<form><sheet><group invisible="c"><field name="a"/></group>'
            '<group><field name="a"/></group></sheet></form>', [self._line('a')])
        self.assertIsNone(tree.xpath('.//field[@name="a"]')[0].get('invisible'))

    def test_twice_seen_field_groups_are_alternatives(self):
        """FR-FVF-0007, BR-FVF-0001: groups of the occurrences are joined; none shows to outsiders."""
        tree = self._run(
            '<form><sheet><group groups="x.g1"><field name="a"/></group>'
            '<group groups="x.g2"><field name="a"/></group></sheet></form>', [self._line('a')])
        node = tree.xpath('.//field[@name="a"]')[0]
        self.assertEqual(node.getparent().get('groups'), 'x.g1,x.g2')

    def test_twice_seen_field_one_occurrence_open_has_no_wrapper(self):
        """FR-FVF-0007: an occurrence open to everyone leaves the moved field open to everyone."""
        tree = self._run(
            '<form><sheet><group groups="x.g1"><field name="a"/></group>'
            '<group><field name="a"/></group></sheet></form>', [self._line('a')])
        node = tree.xpath('.//field[@name="a"]')[0]
        self.assertIsNone(node.getparent().get('groups'))

    def test_moved_label_gets_the_former_container_invisible(self):
        """FR-FVF-0007: the label follows the field's visibility."""
        tree = self._run(
            '<form><sheet><group invisible="c"><label for="a"/><field name="a"/></group></sheet></form>',
            [self._line('a')])
        self.assertEqual(tree.xpath('.//label[@for="a"]')[0].get('invisible'), 'c')

    def test_text_after_a_moved_field_stays_on_the_form(self):
        """FR-FVF-0007: unit text written after a field is not lost."""
        tree = self._run('<form><sheet><group><field name="a"/> days<field name="b"/></group></sheet></form>',
                         [self._line('a')])
        self.assertIn(' days', etree.tostring(tree, encoding='unicode'))

    def test_sections_skip_a_sheet_of_an_inline_subform(self):
        """FR-FVF-0004: without its own sheet the section goes before the chatter, not into a sub-form."""
        tree = self._run(
            '<form><field name="b"><form><sheet/></form></field><field name="a"/><chatter/></form>',
            [self._line('a')])
        self.assertEqual([c.tag for c in tree][-2:], ['div', 'chatter'])

    def test_hide_without_groups_writes_no_groups_attribute(self):
        """FR-FVF-0002: a line hiding from no group leaves the field untouched."""
        tree = self._run('<form><sheet><field name="a"/></sheet></form>', [self._line('a', section_id=False)])
        self.assertIsNone(tree.xpath('.//field[@name="a"]')[0].get('groups'))

    def test_prune_ignores_fields_of_a_list_subview(self):
        """FR-FVF-0004: invisible columns of a placed one2many do not hide the section."""
        tree = etree.fromstring(
            '<form><div class="o_fvf_section" name="fvf_section_1"><group><field name="l">'
            '<list><field name="x" invisible="y"/></list></field></group></div></form>')
        apply.prune_empty_sections(tree)
        self.assertIsNone(tree.xpath('.//div[@name="fvf_section_1"]')[0].get('invisible'))

    def test_hidden_twice_seen_field_keeps_groups_as_alternatives(self):
        """FR-FVF-0007, FR-FVF-0002: hiding from a group does not turn 'either group' into 'both groups'."""
        tree = self._run(
            '<form><sheet><group groups="x.g1"><field name="a"/></group>'
            '<group groups="x.g2"><field name="a"/></group></sheet></form>', [self._line('a', hide_refs=['x.h'])])
        node = tree.xpath('.//field[@name="a"]')[0]
        self.assertEqual(node.get('groups'), '!x.h')
        self.assertEqual([d.get('groups') for d in node.iterancestors('div') if d.get('groups')], ['x.g1,x.g2'])

    def test_hidden_twice_seen_field_open_once_has_no_wrapper(self):
        """FR-FVF-0007, FR-FVF-0002: users outside g1 who saw the open appearance keep the field."""
        tree = self._run(
            '<form><sheet><group groups="x.g1"><field name="a"/></group>'
            '<group><field name="a"/></group></sheet></form>', [self._line('a', hide_refs=['x.h'])])
        node = tree.xpath('.//field[@name="a"]')[0]
        self.assertEqual(node.get('groups'), '!x.h')
        self.assertFalse([d for d in node.iterancestors('div') if d.get('groups')])

    def test_hidden_moved_field_label_gets_the_hide_mark(self):
        """FR-FVF-0002: the moved label is hidden from the same groups as its field."""
        tree = self._run(
            '<form><sheet><group><label for="a"/><field name="a"/></group></sheet></form>',
            [self._line('a', hide_refs=['x.h'])])
        self.assertEqual(tree.xpath('.//label[@for="a"]')[0].get('groups'), '!x.h')

    def test_different_groups_and_rules_never_reveal(self):
        """BR-FVF-0001 (standing answer): shown only where every appearance would show it.

        g1 saw the field while 'not c', g2 while 'c': a g1-only user must not see it while 'c'.
        """
        tree = self._run(
            '<form><sheet><group groups="x.g1" invisible="c"><field name="a"/></group>'
            '<group groups="x.g2" invisible="not c"><field name="a"/></group></sheet></form>', [self._line('a')])
        node = tree.xpath('.//field[@name="a"]')[0]
        self.assertEqual(node.get('invisible'), '(c) or (not c)')
        self.assertEqual([d.get('groups') for d in reversed(list(node.iterancestors('div'))) if d.get('groups')],
                         ['x.g1', 'x.g2'])

    def test_same_groups_different_rules_are_anded(self):
        """FR-FVF-0007: appearances seen by the same groups show the field whenever either showed it."""
        tree = self._run(
            '<form><sheet><group groups="x.g1" invisible="c"><field name="a"/></group>'
            '<group groups="x.g1" invisible="not c"><field name="a"/></group></sheet></form>', [self._line('a')])
        node = tree.xpath('.//field[@name="a"]')[0]
        self.assertEqual(node.get('invisible'), '(c) and (not c)')
        self.assertEqual([d.get('groups') for d in node.iterancestors('div') if d.get('groups')], ['x.g1'])

    def test_skipped_line_logs_the_design_text(self):
        """FR-FVF-0015: a line on a vanished field is skipped with the design's DEBUG line."""
        with self.assertLogs(apply.__name__, level='DEBUG') as logs:
            self._run('<form><sheet><field name="a"/></sheet></form>', [self._line('zz')])
        self.assertIn('fvf: layout 1 skipped missing field zz', logs.output[0])

    def _wrapper_groups(self, tree):
        node = tree.xpath('.//field[@name="a"]')[0]
        return [d.get('groups') for d in reversed(list(node.iterancestors('div'))) if d.get('groups')]

    def test_nested_and_single_group_appearances_stay_alternatives(self):
        """FR-FVF-0007, BR-FVF-0006: g1-and-g2 users and g3-only users both keep the moved field.

        (g1 and g2) or g3 is written as the nested divs 'g1,g3' > 'g2,g3'.
        """
        tree = self._run(
            '<form><sheet><group groups="x.g1"><group groups="x.g2"><field name="a"/></group></group>'
            '<group groups="x.g3"><field name="a"/></group></sheet></form>', [self._line('a')])
        self.assertEqual(self._wrapper_groups(tree), ['x.g1,x.g3', 'x.g2,x.g3'])

    def test_two_nested_appearances_give_every_pick(self):
        """FR-FVF-0007: (g1 and g2) or (g3 and g4) gives one div per pick across the appearances."""
        tree = self._run(
            '<form><sheet><group groups="x.g1"><group groups="x.g2"><field name="a"/></group></group>'
            '<group groups="x.g3"><group groups="x.g4"><field name="a"/></group></group></sheet></form>',
            [self._line('a')])
        self.assertEqual(self._wrapper_groups(tree),
                         ['x.g1,x.g3', 'x.g1,x.g4', 'x.g2,x.g3', 'x.g2,x.g4'])

    def test_negative_group_mark_narrows_the_alternatives(self):
        """BR-FVF-0001: a value holding a '!' mark is joined as is, which only narrows.

        A g2 user who is also in k loses the field; nobody gains it.
        """
        tree = self._run(
            '<form><sheet><group groups="x.g1,!x.k"><field name="a"/></group>'
            '<group groups="x.g2"><field name="a"/></group></sheet></form>', [self._line('a')])
        self.assertEqual(self._wrapper_groups(tree), ['x.g1,!x.k,x.g2'])

    def test_bug_b_0189_label_in_another_container_moves_with_its_field(self):
        """B-0189: a nolabel field whose label sits in an outer cell is moved after that label."""
        tree = self._run(
            '<form><sheet><group><div class="o_wrap_label"><label for="a"/>'
            '<div><field name="a" nolabel="1"/></div></div></group></sheet></form>', [self._line('a')])
        section = tree.xpath('.//div[contains(@class, "o_fvf_section")]')[0]
        self.assertEqual([node.tag for node in section.xpath('./group/*')], ['label', 'field'])
        self.assertEqual(section.xpath('./group/label')[0].get('for'), 'a')
        self.assertFalse(tree.xpath('.//div[@class="o_wrap_label"]'))

    def test_bug_b_0189_label_moves_with_the_field_when_hidden_by_a_condition(self):
        """B-0189: the moved label follows the field's show rule, so no label stays without its value."""
        tree = self._run(
            '<form><sheet><group><div><label for="a"/><div><field name="a" nolabel="1" invisible="c"/></div></div>'
            '</group></sheet></form>', [self._line('a')])
        self.assertEqual(tree.xpath('.//label[@for="a"]')[0].get('invisible'), 'c')
