from lxml import etree

from odoo.tests import Form

from .common import FvfCommon


class TestPreview(FvfCommon):
    """Own tests of the preview (T-006) beside the locked acceptance tests.

    Requirements: FR-FVF-0010, FR-FVF-0011
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.acme = cls.env['res.partner'].create({'name': 'Acme'})
        cls.layout = cls.env['fvf.layout'].create({
            'model_id': cls.env['ir.model']._get('res.partner').id,
            'view_id': cls.env.ref('base.view_partner_form').id,
            'company_id': cls.company_be.id,
        })

    def _preview(self, **values):
        values = dict({
            'layout_id': self.layout.id, 'group_id': self.group_a.id,
            'company_id': self.company_be.id, 'res_id': self.acme.id}, **values)
        return self.env['fvf.layout.preview'].with_user(self.user_admin).create(values)

    def test_company_defaults_to_the_layout_company(self):
        """FR-FVF-0010: the dialog opened from a layout proposes the layout's company (Form in the dialog's context)."""
        form = Form(self.env['fvf.layout.preview'].with_user(self.user_admin).with_context(
            default_layout_id=self.layout.id))
        self.assertEqual(form.company_id, self.company_be)

    def test_company_defaults_to_the_current_one_for_a_default_layout(self):
        """FR-FVF-0010: a shared default layout has no company, so the administrator's current company is proposed."""
        default = self.env['fvf.layout'].create({
            'model_id': self.layout.model_id.id, 'view_id': self.layout.view_id.id, 'company_id': False})
        preview = self.env['fvf.layout.preview'].with_user(self.user_admin).with_context(
            default_layout_id=default.id).default_get(['company_id'])
        self.assertEqual(preview['company_id'], self.user_admin.company_id.id)

    def test_preview_button_opens_the_dialog_for_the_layout(self):
        """FR-FVF-0010: the layout form's Preview button is an action on the dialog, run for the open layout."""
        action = self.env.ref('form_view_field_organizer.action_fvf_layout_preview')
        self.assertEqual((action.res_model, action.target), ('fvf.layout.preview', 'new'))
        form = self.env.ref('form_view_field_organizer.fvf_layout_view_form_preview')
        self.assertIn('name="%d"' % action.id, form.arch_db)

    def test_banner_names_group_company_and_record(self):
        """FR-FVF-0010: the banner reads 'Preview (read-only) as <group> in <company> - <record>'."""
        result = self._preview().get_preview_view()
        self.assertEqual(
            result['banner'], "Preview (read-only) as FVF Test Group A in BE Company - Acme")
        self.assertEqual((result['res_model'], result['res_id']), ('res.partner', self.acme.id))
        self.assertIn('name', result['fields'])

    def test_no_button_or_chatter_remains_anywhere_in_the_arch(self):
        """FR-FVF-0011: header buttons and buttons of embedded lists are removed too, and every edit flag is 0."""
        tree = etree.fromstring(self._preview().get_preview_view()['arch'])
        self.assertFalse(tree.xpath('//button'))
        self.assertFalse(tree.xpath('//chatter'))
        for name in ('edit', 'create', 'delete', 'duplicate'):
            self.assertEqual(tree.get(name), '0')

    def test_unresolved_group_markers_do_not_reach_the_browser(self):
        """FR-FVF-0010: every group restriction is settled on the server."""
        arch = self._preview().get_preview_view()['arch']
        self.assertNotIn('__groups_key__', arch)
