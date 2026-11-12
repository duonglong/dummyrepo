from lxml import etree

from odoo.exceptions import AccessError, UserError
from odoo.tests import Form

from .common import FvfCommon


class TestPreviewAcceptance(FvfCommon):
    """Acceptance tests of T-006: preview a draft as a chosen group, company and record, read-only.

    Requirements: FR-FVF-0010, FR-FVF-0011.
    The choice is made in the preview dialog with Form (group, company,
    record), then get_preview_view() is called; the client action that draws
    the banner and the read-only form is left to the build.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner_model = cls.env['ir.model']._get('res.partner')
        cls.view = cls.env.ref('base.view_partner_form')
        cls.acme = cls.env['res.partner'].create({'name': 'Acme', 'vat': 'BE0123456789'})
        cls.group_other = cls.env['res.groups'].create({'name': 'FVF Other Group'})

    def _layout(self, company=None):
        return self.env['fvf.layout'].create({
            'model_id': self.partner_model.id,
            'view_id': self.view.id,
            'company_id': company.id if company else False,
        })

    def _hide_vat(self, layout, group):
        line = layout.line_ids.filtered(lambda l: l.field_name == 'vat')
        self.assertTrue(line)
        line[:1].hidden_group_ids = [(4, group.id)]

    def _preview(self, layout, group, company, record=None):
        with Form(self.env['fvf.layout.preview'].with_user(self.user_admin).with_context(
                default_layout_id=layout.id)) as form:
            form.group_id = group
            form.company_id = company
            form.res_id = (record or self.acme).id
        return form.save()

    def _tree(self, preview):
        result = preview.with_user(self.user_admin).get_preview_view()
        return etree.fromstring(result['arch'])

    def _has_vat(self, tree):
        return bool(tree.xpath('.//field[@name="vat"]'))

    # -- FR-FVF-0010 ---------------------------------------------------------

    def test_preview_as_a_group_and_company(self):
        """SC-FVF-0010-1 (FR-FVF-0010): as group A Tax ID is absent; as another group it shows."""
        layout = self._layout(self.company_be)
        self._hide_vat(layout, self.group_a)
        as_a = self._tree(self._preview(layout, self.group_a, self.company_be))
        self.assertFalse(self._has_vat(as_a))
        as_other = self._tree(self._preview(layout, self.group_other, self.company_be))
        self.assertTrue(self._has_vat(as_other))

    def test_preview_counts_implied_groups(self):
        """FR-FVF-0010 (BR-FVF-0006): group B implies group A, so a preview as B also hides what A cannot see."""
        layout = self._layout(self.company_be)
        self._hide_vat(layout, self.group_a)
        tree = self._tree(self._preview(layout, self.group_b, self.company_be))
        self.assertFalse(self._has_vat(tree))

    def test_preview_default_draft_for_company_without_own_layout(self):
        """SC-FVF-0010-2 (FR-FVF-0010): a draft default layout is shown in FR Company, which has no layout."""
        default = self._layout()
        self._hide_vat(default, self.group_a)
        tree = self._tree(self._preview(default, self.group_a, self.company_fr))
        self.assertFalse(self._has_vat(tree))

    def test_default_draft_does_not_replace_a_published_company_layout(self):
        """FR-FVF-0010 (BR-FVF-0002): where the company has its own published layout, a draft default is not shown."""
        own = self._layout(self.company_be)
        own.action_publish()
        default = self._layout()
        self._hide_vat(default, self.group_a)
        tree = self._tree(self._preview(default, self.group_a, self.company_be))
        self.assertTrue(self._has_vat(tree))

    def test_unreadable_record_is_refused(self):
        """FR-FVF-0010: a record that cannot be read gives 'The chosen record cannot be shown.'"""
        layout = self._layout(self.company_be)
        preview = self.env['fvf.layout.preview'].with_user(self.user_admin).create({
            'layout_id': layout.id, 'group_id': self.group_a.id,
            'company_id': self.company_be.id, 'res_id': 2 ** 30,
        })
        with self.assertRaisesRegex(UserError, "The chosen record cannot be shown"):
            preview.get_preview_view()

    def test_only_administrators_preview(self):
        """FR-FVF-0010 (NFR-SEC-0001): a user who is not a Settings administrator gets an AccessError."""
        layout = self._layout(self.company_be)
        preview = self._preview(layout, self.group_a, self.company_be)
        with self.assertRaises(AccessError):
            preview.with_user(self.user_ben).get_preview_view()

    # -- FR-FVF-0011 ---------------------------------------------------------

    def test_preview_is_read_only_and_changes_nothing(self):
        """SC-FVF-0011-1 (FR-FVF-0011): no button, no chatter, edit=0; the layout stays draft; others see the original."""
        layout = self._layout(self.company_be)
        self._hide_vat(layout, self.group_a)
        self.env.flush_all()
        stamp = self.acme.write_date
        vat = self.acme.vat
        result = self._preview(layout, self.group_a, self.company_be).with_user(
            self.user_admin).get_preview_view()
        tree = etree.fromstring(result['arch'])
        self.assertFalse(tree.xpath('.//button'))
        self.assertFalse(tree.xpath('.//chatter'))
        self.assertEqual(tree.get('edit'), '0')
        self.assertEqual(tree.get('create'), '0')
        self.assertEqual(tree.get('delete'), '0')
        self.assertEqual(layout.state, 'draft')
        self.env.flush_all()
        self.acme.invalidate_recordset()
        self.assertEqual(self.acme.write_date, stamp)
        self.assertEqual(self.acme.vat, vat)
        other = self.env['res.partner'].with_user(self.user_anna).with_company(self.company_be)
        arch = etree.fromstring(other.get_views([(False, 'form')])['views']['form']['arch'])
        self.assertTrue(arch.xpath('.//field[@name="vat"]'))
