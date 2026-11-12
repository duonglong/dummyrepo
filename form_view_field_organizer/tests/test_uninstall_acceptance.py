from lxml import etree

from .common import FvfCommon


class TestUninstallAcceptance(FvfCommon):
    """Acceptance tests of T-007: the organizer leaves no trace on the original forms.

    Requirements: NFR-REL-0001, FR-FVF-0012 (SC-FVF-0012-1).
    Uninstalling the module cannot run inside a test transaction (it reloads
    the registry), so these tests check what uninstall relies on: publishing
    writes no ir.ui.view row and no contact record, and once the layouts are
    gone (what uninstall removes with the module's data) the form arch equals
    the original arch captured before.  A real uninstall run is left to the
    build's manual check.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner_model = cls.env['ir.model']._get('res.partner')
        cls.view = cls.env.ref('base.view_partner_form')

    def _arch(self):
        partners = self.env['res.partner'].with_user(self.user_ben).with_company(self.company_be)
        arch = partners.get_views([(False, 'form')])['views']['form']['arch']
        return etree.tostring(etree.fromstring(arch))

    def _view_rows(self):
        self.env.flush_all()
        self.env.cr.execute("SELECT id, write_date FROM ir_ui_view ORDER BY id")
        return self.env.cr.fetchall()

    def _publish_layout(self):
        layout = self.env['fvf.layout'].create({
            'model_id': self.partner_model.id,
            'view_id': self.view.id,
        })
        layout.line_ids.filtered(lambda l: l.field_name == 'website').hidden_group_ids = [
            (4, self.env.ref('base.group_user').id)]
        section = self.env['fvf.layout.section'].create({'layout_id': layout.id, 'name': 'Extras'})
        layout.line_ids.filtered(lambda l: l.field_name == 'function').section_id = section
        layout.action_publish()
        return layout

    def test_publishing_creates_or_changes_no_view_row(self):
        """SC-FVF-0012-1 / NFR-REL-0001: view count and write dates are the same before and after publishing."""
        rows_before = self._view_rows()
        original_arch = self.view.arch_db
        self._publish_layout()
        self.assertEqual(self._view_rows(), rows_before)
        self.view.invalidate_recordset()
        self.assertEqual(self.view.arch_db, original_arch)

    def test_publishing_changes_no_contact_record(self):
        """SC-FVF-0012-1: no contact record is changed by publishing."""
        partner = self.env['res.partner'].create({'name': 'Untouched', 'website': 'https://x.test', 'function': 'CEO'})
        query = "SELECT write_date, website, function FROM res_partner WHERE id = %s"
        self.env.flush_all()
        self.env.cr.execute(query, [partner.id])
        before = self.env.cr.fetchone()
        self._publish_layout()
        self.env.flush_all()
        self.env.cr.execute(query, [partner.id])
        self.assertEqual(self.env.cr.fetchone(), before)

    def test_form_is_the_original_once_layouts_are_gone(self):
        """NFR-REL-0001: after the layouts are removed the form arch equals the arch captured before."""
        original = self._arch()
        layout = self._publish_layout()
        self.assertNotEqual(self._arch(), original, "the layout must show before it is removed")
        self.env['fvf.layout'].with_context(active_test=False).search([]).unlink()
        self.assertFalse(layout.exists())
        self.assertEqual(self._arch(), original)

    def test_no_view_inherits_a_layout_into_the_form(self):
        """NFR-REL-0001: no inheriting view carries a layout, so nothing needs repair on uninstall."""
        self._publish_layout()
        children = self.env['ir.ui.view'].search([('inherit_id', '=', self.view.id), ('arch_db', 'ilike', 'fvf')])
        self.assertFalse(children)
