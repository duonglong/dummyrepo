"""Acceptance tests of T-002: central attachment search with filters.

Requirements: BR-AM-0001, BR-AM-0002, FR-AM-0001, FR-AM-0002, FR-AM-0003,
FR-AM-0004, FR-AM-0005, NFR-AM-0001.

The Attachments menu is found through the registry (a menu named
"Attachments" outside the base module that opens ir.attachment); its action
domain is applied to the search, as the list screen does. The search-view
click steps (filters, group-bys) are left to the build: the same criteria
are given as domains and grouped through _read_group.
"""
import base64

from odoo.tests.common import TransactionCase, new_test_user, tagged
from odoo.tools.safe_eval import safe_eval


@tagged('post_install', '-at_install')
class TestSearchAcceptance(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Users = cls.env['res.users']
        cls.user_a = new_test_user(cls.env, login='am_user_a', groups='base.group_user')
        cls.user_b = new_test_user(cls.env, login='am_user_b', groups='base.group_user')
        cls.marc = new_test_user(cls.env, login='am_marc', groups='base.group_user')
        cls.admin = cls.env.ref('base.user_admin')
        Partner = cls.env['res.partner']
        cls.azure = Partner.create({'name': 'Azure Interior AM'})
        cls.deco = Partner.create({'name': 'Deco Addict AM'})
        cls.hidden = cls.env['ir.config_parameter'].sudo().create(
            {'key': 'am.acceptance.hidden', 'value': '1'})

    # -- helpers ---------------------------------------------------------
    def _attach(self, name, mimetype, uploader=None, record=None, res_field=False,
                content=b'am-content'):
        vals = {
            'name': name,
            'mimetype': mimetype,
            'raw': content + name.encode(),
            'res_field': res_field,
        }
        if record is not None:
            vals.update(res_model=record._name, res_id=record.id)
        att = self.env['ir.attachment'].with_user(uploader or self.env.user).sudo()
        return att.create(vals)

    def _menu_action(self):
        menus = self.env['ir.ui.menu'].sudo().search([('name', '=', 'Attachments')])
        menus = menus.filtered(
            lambda m: m.action and m.action._name == 'ir.actions.act_window'
            and m.action.res_model == 'ir.attachment'
            and not (m.get_external_id().get(m.id) or '').startswith('base.'))
        self.assertTrue(menus, "No Attachments menu of the module opens ir.attachment")
        return menus[0], menus[0].action

    def _listed(self, user, extra=None):
        _menu, action = self._menu_action()
        domain = safe_eval(action.domain or '[]') + (extra or [])
        return self.env['ir.attachment'].with_user(user).search(domain)

    # -- FR-AM-0001 ------------------------------------------------------
    def test_sc_0001_1_menu_lists_attachments_of_every_kind_of_record(self):
        """Requirements: FR-AM-0001. SC-AM-0001-1: a PDF on a contact and an image
        on a sales-order-like record both appear in one list with the columns."""
        pdf = self._attach('am1.pdf', 'application/pdf', self.user_a, self.azure)
        img = self._attach('am1.png', 'image/png', self.user_a, self.deco)
        menu, action = self._menu_action()
        visible = self.env['ir.ui.menu'].with_user(self.user_a).search([('id', '=', menu.id)])
        self.assertTrue(visible, "The Attachments menu must be visible to internal users")
        self.assertIn('list', action.view_mode)
        listed = self._listed(self.user_a)
        self.assertIn(pdf, listed)
        self.assertIn(img, listed)
        list_view = action.view_ids.filtered(lambda v: v.view_mode == 'list')[:1].view_id
        arch = self.env['ir.attachment'].with_user(self.user_a).get_view(
            view_id=list_view.id or False, view_type='list')['arch']
        for fname in ('name', 'create_uid', 'file_type_group', 'file_size', 'create_date'):
            self.assertIn('name="%s"' % fname, arch)
        self.assertTrue('name="res_model_id"' in arch or 'name="res_model"' in arch)
        self.assertTrue('name="record_label"' in arch or 'name="res_id"' in arch)

    def test_sc_0001_2_field_storage_file_is_not_listed(self):
        """Requirements: FR-AM-0001. SC-AM-0001-2: the picture file stored for a
        contact's image field does not appear."""
        picture = self._attach('am-picture.png', 'image/png', self.user_a, self.azure,
                               res_field='image_1920')
        doc = self._attach('am-doc.png', 'image/png', self.user_a, self.azure)
        listed = self._listed(self.admin)
        self.assertNotIn(picture, listed)
        self.assertIn(doc, listed)

    # -- FR-AM-0002 / BR-AM-0001 ----------------------------------------
    def test_sc_0002_1_only_attachments_of_openable_records(self):
        """Requirements: FR-AM-0002, BR-AM-0001. SC-AM-0002-1: only the file on the
        record user A can open is listed, and the count shows 1."""
        ok = self._attach('am-ok.pdf', 'application/pdf', self.admin, self.azure)
        hidden = self._attach('am-hidden.pdf', 'application/pdf', self.admin, self.hidden)
        mine = [('id', 'in', (ok | hidden).ids)]
        listed = self._listed(self.user_a, mine)
        self.assertEqual(listed, ok)
        _menu, action = self._menu_action()
        domain = safe_eval(action.domain or '[]') + mine
        self.assertEqual(self.env['ir.attachment'].with_user(self.user_a).search_count(domain), 1)
        groups = self.env['ir.attachment'].with_user(self.user_a)._read_group(
            domain, groupby=['res_model_id'], aggregates=['__count'])
        self.assertEqual(sum(g[1] for g in groups), 1)

    # -- FR-AM-0003 / BR-AM-0002 ----------------------------------------
    def test_sc_0003_1_attachment_without_record_for_uploader_and_admin(self):
        """Requirements: FR-AM-0003, BR-AM-0002. SC-AM-0003-1: uploader and
        administrator see it; another user does not."""
        orphan = self._attach('am-orphan.pdf', 'application/pdf', self.user_a)
        self.assertFalse(orphan.res_model)
        scope = [('id', '=', orphan.id)]
        self.assertEqual(self._listed(self.user_a, scope), orphan)
        self.assertEqual(self._listed(self.admin, scope), orphan)
        self.assertFalse(self._listed(self.user_b, scope))

    # -- FR-AM-0004 ------------------------------------------------------
    def test_sc_0004_1_kind_record_and_owner_combine(self):
        """Requirements: FR-AM-0004. SC-AM-0004-1: kind Contact, record Azure
        Interior and owner Marc give only Marc's files on Azure Interior."""
        target = self._attach('am4-target.pdf', 'application/pdf', self.marc, self.azure)
        self._attach('am4-other-owner.pdf', 'application/pdf', self.user_a, self.azure)
        self._attach('am4-other-record.pdf', 'application/pdf', self.marc, self.deco)
        partner_model = self.env['ir.model']._get_id('res.partner')
        scope = [('name', '=like', 'am4-%')]
        listed = self._listed(self.admin, scope + [
            ('res_model_id', '=', partner_model),
            ('res_id', '=', self.azure.id),
            ('create_uid', '=', self.marc.id),
        ])
        self.assertEqual(listed, target)
        self.assertEqual(target.res_model_id.id, partner_model)
        self.assertIn('Azure Interior AM', target.record_label)

    def test_sc_0004_2_group_by_owner(self):
        """Requirements: FR-AM-0004. Grouping by owner and kind of record counts each owner's files."""
        self._attach('am4g-1.pdf', 'application/pdf', self.marc, self.azure)
        self._attach('am4g-2.pdf', 'application/pdf', self.marc, self.azure)
        self._attach('am4g-3.pdf', 'application/pdf', self.user_a, self.azure)
        groups = self.env['ir.attachment']._read_group(
            [('name', '=like', 'am4g-%')], groupby=['create_uid', 'res_model_id'],
            aggregates=['__count'])
        counts = {g[0].id: g[2] for g in groups}
        self.assertEqual(counts[self.marc.id], 2)
        self.assertEqual(counts[self.user_a.id], 1)

    # -- FR-AM-0005 ------------------------------------------------------
    def test_sc_0005_1_filter_by_image(self):
        """Requirements: FR-AM-0005. SC-AM-0005-1: PDF, PNG and CSV; Image lists
        only the PNG."""
        pdf = self._attach('am5.pdf', 'application/pdf', self.user_a, self.azure)
        png = self._attach('am5.png', 'image/png', self.user_a, self.azure)
        csv = self._attach('am5.csv', 'text/csv', self.user_a, self.azure)
        scope = [('id', 'in', (pdf | png | csv).ids)]
        self.assertEqual(self._listed(self.user_a, scope + [('file_type_group', '=', 'image')]), png)
        self.assertEqual(self._listed(self.user_a, scope + [('file_type_group', '=', 'pdf')]), pdf)

    def test_sc_0005_2_unusual_format_is_other(self):
        """Requirements: FR-AM-0005. SC-AM-0005-2: a CAD drawing is listed under Other."""
        cad = self._attach('am5.dwg', 'application/acad', self.user_a, self.azure)
        png = self._attach('am5o.png', 'image/png', self.user_a, self.azure)
        scope = [('id', 'in', (cad | png).ids)]
        self.assertEqual(self._listed(self.user_a, scope + [('file_type_group', '=', 'other')]), cad)

    def test_file_type_groups_are_the_seven_types(self):
        """Requirements: FR-AM-0005. Exactly seven groups, one per attachment."""
        field = self.env['ir.attachment']._fields['file_type_group']
        codes = {code for code, _label in field._description_selection(self.env)}
        self.assertEqual(codes, {'pdf', 'image', 'document', 'spreadsheet',
                                 'presentation', 'archive', 'other'})
        for mime, expected in (('application/pdf', 'pdf'), ('image/jpeg', 'image'),
                               ('application/zip', 'archive')):
            att = self._attach('am5-%s' % expected, mime, self.user_a, self.azure)
            self.assertEqual(att.file_type_group, expected)

    # -- NFR-AM-0001 -----------------------------------------------------
    def test_nfr_0001_helpers_are_stored_and_indexed(self):
        """Requirements: NFR-AM-0001. A 100,000-row timing is not run here; the
        stored, indexed helper columns the filters rely on are checked instead."""
        fields = self.env['ir.attachment']._fields
        for fname in ('file_type_group', 'res_model_id'):
            self.assertTrue(fields[fname].store, fname)
            self.assertTrue(fields[fname].index, fname)
        self.assertTrue(fields['record_label'].store)
