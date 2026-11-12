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
