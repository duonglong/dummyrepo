import ast
import fnmatch

from lxml import etree

from odoo.tests.common import TransactionCase
from odoo.tools import file_path

MODULE = 'form_view_field_organizer'
FILES = {
    'section_js': 'static/src/fvf_section/fvf_section.js',
    'section_xml': 'static/src/fvf_section/fvf_section.xml',
    'section_scss': 'static/src/fvf_section/fvf_section.scss',
    'compiler_js': 'static/src/fvf_section/form_compiler.js',
    'lines_js': 'static/src/fvf_layout_lines/fvf_layout_lines.js',
    'lines_xml': 'static/src/fvf_layout_lines/fvf_layout_lines.xml',
    'lines_scss': 'static/src/fvf_layout_lines/fvf_layout_lines.scss',
}


class TestSectionWidgetAcceptance(TransactionCase):
    """Acceptance tests of T-005: what the section block and the line editor widget must provide.

    Requirements: FR-FVF-0003, FR-FVF-0004, FR-FVF-0006, FR-FVF-0007.
    The folding, the start state on screen and the dragging are browser
    behaviour (Hoot tests of the build); here the files are checked to exist,
    to be loaded in the backend bundle, to carry the control the scenarios
    name, and the layout form to attach the widget to its lines.
    """

    def _read(self, key):
        path = file_path('%s/%s' % (MODULE, FILES[key]))
        with open(path, encoding='utf-8') as handle:
            return handle.read()

    def _backend_assets(self):
        manifest = file_path('%s/__manifest__.py' % MODULE)
        with open(manifest, encoding='utf-8') as handle:
            return ast.literal_eval(handle.read()).get('assets', {}).get('web.assets_backend', [])

    def test_component_files_are_loaded_in_the_backend(self):
        """FR-FVF-0003: the section block and the lines widget files are part of web.assets_backend."""
        entries = [e for e in self._backend_assets() if isinstance(e, str)]
        for rel in FILES.values():
            full = '%s/%s' % (MODULE, rel)
            self.assertTrue(
                any(e == full or fnmatch.fnmatch(full, e) for e in entries),
                "%s is not in web.assets_backend" % full)

    def test_section_title_is_a_real_toggle_button(self):
        """SC-FVF-0003-1 (FR-FVF-0003): the title is a button that folds and unfolds, with aria-expanded."""
        xml = self._read('section_xml')
        self.assertIn('o_fvf_section_toggle', xml)
        self.assertIn('aria-expanded', xml)
        self.assertIn('aria-controls', xml)
        self.assertIn('type="button"', xml)
        self.assertIn('d-none', xml)

    def test_section_component_is_compiled_from_the_section_block(self):
        """SC-FVF-0004-1 (FR-FVF-0004): the form compiler picks the section block and starts it from start_closed."""
        compiler = self._read('compiler_js')
        self.assertIn('o_fvf_section', compiler)
        self.assertIn('form_compilers', compiler)
        self.assertIn('start_closed', compiler + self._read('section_js'))

    def test_lines_widget_is_registered_and_sortable(self):
        """FR-FVF-0006 / FR-FVF-0007: the lines widget is registered as fvf_layout_lines and can drag rows."""
        script = self._read('lines_js')
        self.assertIn('fvf_layout_lines', script)
        self.assertIn('useSortable', script)
        self.assertIn('section_id', script)
        self.assertIn('sequence', script)

    def test_lines_widget_has_an_original_place_block_and_missing_mark(self):
        """SC-FVF-0007-2 (FR-FVF-0007): a block for fields not in a section; a text mark for a missing field."""
        template = self._read('lines_xml')
        self.assertIn('Original place (not in a section)', template)
        self.assertIn('Missing field', template)

    def test_layout_form_attaches_the_widget_to_the_lines(self):
        """FR-FVF-0006: the layout form shows its lines through the fvf_layout_lines widget."""
        arch = self.env['fvf.layout'].get_views([(False, 'form')])['views']['form']['arch']
        tree = etree.fromstring(arch)
        self.assertTrue(tree.xpath('.//field[@name="line_ids"][@widget="fvf_layout_lines"]'))
