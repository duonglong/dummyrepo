from lxml import etree

from odoo.exceptions import UserError
from odoo.tools import mute_logger
from odoo.addons.form_view_field_organizer.models.fvf_layout import FvfLayout

from .common import FvfCommon


class TestPublish(FvfCommon):
    """Publish, withdraw, copy and lock of layouts beyond the acceptance tests.

    Requirements: BR-FVF-0003, FR-FVF-0012, FR-FVF-0013, FR-FVF-0014
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner_model = cls.env['ir.model']._get('res.partner')
        cls.view = cls.env.ref('base.view_partner_form')

    def _layout(self, company=None):
        return self.env['fvf.layout'].create({
            'model_id': self.partner_model.id, 'view_id': self.view.id,
            'company_id': company.id if company else False})

    def _line(self, layout, name):
        return layout.line_ids.filtered(lambda line: line.field_name == name)

    def _has_field(self, user, name):
        partners = self.env['res.partner'].with_user(user).with_company(self.company_be)
        arch = partners.get_views([(False, 'form')])['views']['form']['arch']
        return bool(etree.fromstring(arch).xpath('.//field[@name="%s"][not(ancestor::field)]' % name))

    def test_publishing_the_copy_archives_its_origin(self):
        """FR-FVF-0014: the origin of a published draft copy is archived, not put back as a draft."""
        live = self._layout(self.company_be)
        live.action_publish()
        live.action_edit()
        draft = self.env['fvf.layout'].search([('origin_id', '=', live.id)])
        draft.action_publish()
        self.assertFalse(live.active)
        self.assertEqual(live.state, 'draft')
        self.assertFalse(draft.origin_id)

    def test_discarding_a_draft_copy_keeps_the_published_layout(self):
        """FR-FVF-0014: discarding the copy deletes it; users keep the published layout."""
        live = self._layout(self.company_be)
        self._line(live, 'website').hidden_group_ids = [(4, self.env.ref('base.group_user').id)]
        live.action_publish()
        live.action_edit()
        draft = self.env['fvf.layout'].search([('origin_id', '=', live.id)])
        action = draft.action_discard_draft_copy()
        self.assertFalse(draft.exists())
        self.assertEqual(action['res_id'], live.id)
        self.assertFalse(self._has_field(self.user_ben, 'website'))

    def test_edit_copy_keeps_sections_lines_and_hidden_groups(self):
        """FR-FVF-0014: the draft copy holds the lines of the origin, pointing at the copied sections."""
        live = self._layout(self.company_be)
        section = self.env['fvf.layout.section'].create({'layout_id': live.id, 'name': 'Accounting'})
        line = self._line(live, 'vat')
        line.write({'section_id': section.id, 'hidden_group_ids': [(4, self.group_a.id)]})
        live.action_publish()
        live.action_edit()
        draft = self.env['fvf.layout'].search([('origin_id', '=', live.id)])
        copied = self._line(draft, 'vat')
        self.assertEqual(copied.section_id.layout_id, draft)
        self.assertNotEqual(copied.section_id, section)
        self.assertEqual(copied.hidden_group_ids, self.group_a)

    def test_duplicate_is_an_unpublished_draft(self):
        """FR-FVF-0014: duplicating a published layout gives a draft with its own sections."""
        live = self._layout(self.company_be)
        self.env['fvf.layout.section'].create({'layout_id': live.id, 'name': 'Accounting'})
        live.action_publish()
        duplicate = live.copy()
        self.assertEqual(duplicate.state, 'draft')
        self.assertEqual(duplicate.section_ids.layout_id, duplicate)

    def test_deleting_a_published_layout_restores_the_original_form(self):
        """FR-FVF-0013: deleting a published layout removes its addition from the forms."""
        live = self._layout(self.company_be)
        self._line(live, 'website').hidden_group_ids = [(4, self.env.ref('base.group_user').id)]
        live.action_publish()
        self.assertFalse(self._has_field(self.user_ben, 'website'))
        live.unlink()
        self.assertTrue(self._has_field(self.user_ben, 'website'))

    @staticmethod
    def _broken_spec(layout):
        raise RuntimeError('boom')

    def test_failing_engine_returns_the_original_arch(self):
        """FR-FVF-0012: a layout the engine cannot apply never stops the form from opening."""
        live = self._layout(self.company_be)
        self._line(live, 'website').hidden_group_ids = [(4, self.env.ref('base.group_user').id)]
        live.action_publish()
        self.patch(FvfLayout, '_fvf_spec', self._broken_spec)
        with mute_logger('odoo.addons.form_view_field_organizer.models.fvf_layout'):
            self.assertTrue(self._has_field(self.user_ben, 'website'))

    def test_published_layout_refuses_conditions_and_unlink(self):
        """FR-FVF-0014: a published layout's lines cannot be added to, changed or deleted."""
        live = self._layout(self.company_be)
        line = self._line(live, 'website')
        live.action_publish()
        message = "A published layout cannot be changed"
        with self.assertRaisesRegex(UserError, message):
            line.unlink()
        with self.assertRaisesRegex(UserError, message):
            live.name = 'Other'
        with self.assertRaisesRegex(UserError, message):
            self.env['fvf.layout.condition'].create({
                'line_id': line.id, 'field_name': 'phone', 'operator': 'set'})

    def test_hidden_group_by_id_when_it_has_no_external_id(self):
        """FR-FVF-0012: a group without an external id is named by its id in the spec."""
        layout = self._layout(self.company_be)
        self._line(layout, 'website').hidden_group_ids = [(4, self.group_a.id), (4, self.env.ref('base.group_user').id)]
        spec = layout._fvf_spec()
        refs = next(line['hide_refs'] for line in spec['lines'] if line['field'] == 'website')
        self.assertEqual(refs, sorted([str(self.group_a.id), 'base.group_user']))

    def test_layout_for_prefers_company_then_default(self):
        """BR-FVF-0002: company entry, else default entry, else none."""
        default = self._layout()
        default.action_publish()
        own = self._layout(self.company_be)
        own.action_publish()
        Layout = self.env['fvf.layout']
        self.assertEqual(Layout._fvf_layout_for('res.partner', self.view.id, self.company_be.id), own)
        self.assertEqual(Layout._fvf_layout_for('res.partner', self.view.id, self.company_fr.id), default)
        self.assertFalse(Layout._fvf_layout_for('res.users', self.view.id, self.company_fr.id))

    def test_cache_token_empty_without_layouts(self):
        """NFR-PERF-0001: a model without a layout keeps one cache entry."""
        self.assertEqual(self.env['fvf.layout']._fvf_cache_token('res.country'), ())
