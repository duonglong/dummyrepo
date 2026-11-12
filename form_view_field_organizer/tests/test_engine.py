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
