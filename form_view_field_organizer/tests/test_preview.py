from lxml import etree

from odoo.exceptions import UserError
from odoo.tests import Form
from odoo.tools.safe_eval import safe_eval

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

    def test_company_defaults_to_the_current_one_when_the_layout_company_is_not_active(self):
        """FR-FVF-0010: the proposed company is one the dialog's company list offers (Form in the dialog's context)."""
        form = Form(self.env['fvf.layout.preview'].with_user(self.user_admin).with_context(
            default_layout_id=self.layout.id, allowed_company_ids=[self.company_fr.id]))
        self.assertEqual(form.company_id, self.company_fr)

    def test_record_outside_the_chosen_company_is_refused(self):
        """FR-FVF-0010: the record is checked in the chosen company only, as the browser reads it.

        The administrator is active in BE and FR; a customer contact of BE previewed in FR
        gets the designed refusal, not a raw access error in the browser.
        """
        customer_be = self.env['res.partner'].create({'name': 'BE customer', 'company_id': self.company_be.id})
        preview = self._preview(company_id=self.company_fr.id, res_id=customer_be.id).with_context(
            allowed_company_ids=(self.company_be | self.company_fr).ids)
        with self.assertRaisesRegex(UserError, "The chosen record cannot be shown."):
            preview.get_preview_view()

    def test_preview_button_opens_the_dialog_for_the_layout(self):
        """FR-FVF-0010: the action behind the Preview button opens the dialog as a popup, defaulting to the open layout's company."""
        action = self.env.ref('form_view_field_organizer.action_fvf_layout_preview')
        self.assertEqual((action.res_model, action.target), ('fvf.layout.preview', 'new'))
        context = safe_eval(action.context, {'active_id': self.layout.id})
        defaults = self.env['fvf.layout.preview'].with_user(self.user_admin).with_context(**context).default_get(['company_id'])
        self.assertEqual(defaults['company_id'], self.layout.company_id.id)

    def test_preview_returns_the_chosen_company_for_the_browser(self):
        """FR-FVF-0010: the result names the chosen company so the form is read in it, not in the active companies."""
        preview = self._preview()
        self.assertEqual(preview.get_preview_view()['company_id'], preview.company_id.id)

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
