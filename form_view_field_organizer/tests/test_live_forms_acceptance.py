from lxml import etree

from odoo.exceptions import UserError
from odoo.tools import mute_logger

from .common import FvfCommon

SECTION = 'o_fvf_section'


class TestLiveFormsAcceptance(FvfCommon):
    """Acceptance tests of T-004: publish, withdraw, edit by draft, live forms per company.

    Requirements: BR-FVF-0002, BR-FVF-0003, FR-FVF-0003, FR-FVF-0004,
    FR-FVF-0006, FR-FVF-0007, FR-FVF-0008, FR-FVF-0009, FR-FVF-0012,
    FR-FVF-0013, FR-FVF-0014.
    Forms are read as a user does, through get_views of res.partner with the
    user and company chosen; the publish/withdraw/edit buttons are tested
    through their methods (action_publish, action_withdraw, action_edit); the
    drawing of the buttons is left to the build.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner_model = cls.env['ir.model']._get('res.partner')
        cls.view = cls.env.ref('base.view_partner_form')
        cls.group_user = cls.env.ref('base.group_user')

    # -- helpers ------------------------------------------------------------

    def _layout(self, company=None):
        return self.env['fvf.layout'].create({
            'model_id': self.partner_model.id,
            'view_id': self.view.id,
            'company_id': company.id if company else False,
        })

    def _line(self, layout, name):
        line = layout.line_ids.filtered(lambda l: l.field_name == name)
        self.assertTrue(line, "no line for %s" % name)
        return line[:1]

    def _hide(self, layout, name, group=None):
        self._line(layout, name).hidden_group_ids = [(4, (group or self.group_user).id)]

    def _section(self, layout, name, start_state=None):
        vals = {'layout_id': layout.id, 'name': name}
        if start_state:
            vals['start_state'] = start_state
        return self.env['fvf.layout.section'].create(vals)

    def _place(self, layout, name, section, sequence=10):
        self._line(layout, name).write({'section_id': section.id, 'sequence': sequence})

    def _arch(self, user, company=None):
        partners = self.env['res.partner'].with_user(user).with_company(company or user.company_id)
        arch = partners.get_views([(False, 'form')])['views']['form']['arch']
        return etree.fromstring(arch)

    def _has_field(self, tree, name):
        return bool(tree.xpath('.//field[@name="%s"][not(ancestor::field)]' % name))

    def _section_node(self, tree, title):
        nodes = tree.xpath('.//div[contains(concat(" ", @class, " "), " %s ")][@string="%s"]' % (SECTION, title))
        return nodes[0] if nodes else None

    def _field_names(self, node):
        return [f.get('name') for f in node.xpath('.//field[not(ancestor::field)]')]

    # -- FR-FVF-0008 / FR-FVF-0009 / BR-FVF-0002 ------------------------------

    def test_company_layout_applies_to_its_users(self):
        """SC-FVF-0008-1 (FR-FVF-0008): BE layout hides Website, the default shows it; a BE user does not see it."""
        default = self._layout()
        default.action_publish()
        be = self._layout(self.company_be)
        self._hide(be, 'website')
        be.action_publish()
        self.assertFalse(self._has_field(self._arch(self.user_ben, self.company_be), 'website'))

    def test_switching_company_switches_layout(self):
        """SC-FVF-0008-2 (FR-FVF-0008): the same user in FR Company, which has no layout, sees the default one."""
        default = self._layout()
        default.action_publish()
        be = self._layout(self.company_be)
        self._hide(be, 'website')
        be.action_publish()
        self.assertFalse(self._has_field(self._arch(self.user_ben, self.company_be), 'website'))
        self.assertTrue(self._has_field(self._arch(self.user_ben, self.company_fr), 'website'))

    def test_shared_default_used_without_own_layout(self):
        """SC-FVF-0009-1 (FR-FVF-0009): FR Company has no layout; the published default hides Website for its user."""
        default = self._layout()
        self._hide(default, 'website')
        default.action_publish()
        self.assertFalse(self._has_field(self._arch(self.user_ben, self.company_fr), 'website'))

    def test_no_layout_shows_original_form(self):
        """SC-FVF-0009-2 (FR-FVF-0009): FR Company has no layout and there is no default; the original form shows."""
        be = self._layout(self.company_be)
        self._hide(be, 'website')
        section = self._section(be, 'Accounting')
        self._place(be, 'vat', section)
        be.action_publish()
        tree = self._arch(self.user_ben, self.company_fr)
        self.assertTrue(self._has_field(tree, 'website'))
        self.assertIsNone(self._section_node(tree, 'Accounting'))

    def test_company_layout_wins_over_default(self):
        """BR-FVF-0002: the company's own layout comes before the default (default hides vat, own one hides nothing)."""
        default = self._layout()
        self._hide(default, 'vat')
        default.action_publish()
        own = self._layout(self.company_be)
        own.action_publish()
        self.assertTrue(self._has_field(self._arch(self.user_ben, self.company_be), 'vat'))
        self.assertFalse(self._has_field(self._arch(self.user_ben, self.company_fr), 'vat'))

    # -- FR-FVF-0012 / BR-FVF-0003 -----------------------------------------------

    def test_publish_leaves_original_form_and_records_unchanged(self):
        """SC-FVF-0012-1 (FR-FVF-0012): users see the layout; the original view and every partner are unchanged."""
        self.env.flush_all()
        arch_before = self.view.arch_db
        views_before = self.env['ir.ui.view'].with_context(active_test=False).search_count([])
        partners = self.env['res.partner'].search([])
        stamps_before = partners.mapped('write_date')
        layout = self._layout(self.company_be)
        self._hide(layout, 'website')
        layout.action_publish()
        self.env.flush_all()
        self.assertFalse(self._has_field(self._arch(self.user_ben, self.company_be), 'website'))
        self.view.invalidate_recordset()
        self.assertEqual(self.view.arch_db, arch_before)
        self.assertEqual(
            self.env['ir.ui.view'].with_context(active_test=False).search_count([]), views_before)
        self.assertEqual(partners.mapped('write_date'), stamps_before)

    def test_publish_sets_state_date_and_user(self):
        """FR-FVF-0012: publishing marks the layout published with its date and publisher."""
        layout = self._layout(self.company_be)
        layout.with_user(self.user_admin).action_publish()
        self.assertEqual(layout.state, 'published')
        self.assertTrue(layout.published_date)
        self.assertEqual(layout.published_uid, self.user_admin)

    def test_publish_replaces_earlier_layout(self):
        """SC-FVF-0012-2 (BR-FVF-0003): publishing L2 for BE Company puts L1 back to draft; users see L2."""
        l1 = self._layout(self.company_be)
        self._hide(l1, 'website')
        l1.action_publish()
        l2 = self._layout(self.company_be)
        self._hide(l2, 'vat')
        l2.action_publish()
        self.assertEqual(l2.state, 'published')
        self.assertEqual(l1.state, 'draft')
        tree = self._arch(self.user_ben, self.company_be)
        self.assertTrue(self._has_field(tree, 'website'))
        self.assertFalse(self._has_field(tree, 'vat'))

    def test_only_a_draft_can_be_published(self):
        """FR-FVF-0012: publishing a published layout is refused."""
        layout = self._layout(self.company_be)
        layout.action_publish()
        with self.assertRaisesRegex(UserError, "Only a draft layout can be published"):
            layout.action_publish()

    def test_one_published_layout_per_form_and_company(self):
        """BR-FVF-0003: two published layouts of the same form and company cannot exist (also for the default)."""
        for company in (self.company_be, None):
            first = self._layout(company)
            first.action_publish()
            second = self._layout(company)
            with mute_logger('odoo.sql_db'), self.assertRaises(Exception):
                with self.env.cr.savepoint():
                    second.write({'state': 'published'})
                    self.env.flush_all()
            second.invalidate_recordset()

    # -- FR-FVF-0013 -----------------------------------------------------------------

    def test_withdraw_restores_original_and_keeps_draft(self):
        """SC-FVF-0013-1 (FR-FVF-0013): after withdrawing, BE users see the original form and the layout is a draft."""
        layout = self._layout(self.company_be)
        self._hide(layout, 'website')
        layout.action_publish()
        self.assertFalse(self._has_field(self._arch(self.user_ben, self.company_be), 'website'))
        layout.action_withdraw()
        self.assertEqual(layout.state, 'draft')
        self.assertTrue(layout.active)
        self.assertTrue(self._has_field(self._arch(self.user_ben, self.company_be), 'website'))

    def test_withdraw_falls_back_to_shared_default(self):
        """FR-FVF-0013: users of the company then see the shared default layout."""
        default = self._layout()
        self._hide(default, 'vat')
        default.action_publish()
        own = self._layout(self.company_be)
        self._hide(own, 'website')
        own.action_publish()
        own.action_withdraw()
        tree = self._arch(self.user_ben, self.company_be)
        self.assertTrue(self._has_field(tree, 'website'))
        self.assertFalse(self._has_field(tree, 'vat'))

    def test_only_a_published_layout_can_be_withdrawn(self):
        """FR-FVF-0013: withdrawing a draft is refused."""
        layout = self._layout(self.company_be)
        with self.assertRaisesRegex(UserError, "Only a published layout can be withdrawn"):
            layout.action_withdraw()

    def test_archiving_a_published_layout_withdraws_it(self):
        """FR-FVF-0013 / design: archiving a published layout withdraws it from the forms."""
        layout = self._layout(self.company_be)
        self._hide(layout, 'website')
        layout.action_publish()
        layout.active = False
        self.assertTrue(self._has_field(self._arch(self.user_ben, self.company_be), 'website'))

    # -- FR-FVF-0014 ------------------------------------------------------------------

    def test_edits_stay_hidden_until_published(self):
        """SC-FVF-0014-1 (FR-FVF-0014): edits on the draft copy reach users only when it is published."""
        live = self._layout(self.company_be)
        live.action_publish()
        action = live.action_edit()
        self.assertEqual(action.get('res_model'), 'fvf.layout')
        draft = self.env['fvf.layout'].search([('origin_id', '=', live.id)])
        self.assertEqual(len(draft), 1)
        self.assertEqual(draft.state, 'draft')
        self._hide(draft, 'website')
        self.assertTrue(self._has_field(self._arch(self.user_ben, self.company_be), 'website'))
        self.assertEqual(live.state, 'published')
        draft.action_publish()
        self.assertFalse(self._has_field(self._arch(self.user_ben, self.company_be), 'website'))
        self.assertFalse(live.active and live.state == 'published')

    def test_edit_twice_reuses_the_single_draft_copy(self):
        """FR-FVF-0014: Edit again opens the same draft copy instead of a second one."""
        live = self._layout(self.company_be)
        self._section(live, 'Accounting')
        live.action_publish()
        live.action_edit()
        live.action_edit()
        drafts = self.env['fvf.layout'].search([('origin_id', '=', live.id)])
        self.assertEqual(len(drafts), 1)
        self.assertEqual(drafts.section_ids.mapped('name'), ['Accounting'])

    def test_published_layout_refuses_changes(self):
        """FR-FVF-0014: a published layout's sections, lines and conditions cannot be changed."""
        live = self._layout(self.company_be)
        section = self._section(live, 'Accounting')
        line = self._line(live, 'website')
        live.action_publish()
        message = "A published layout cannot be changed"
        with self.assertRaisesRegex(UserError, message):
            line.hidden_group_ids = [(4, self.group_user.id)]
        with self.assertRaisesRegex(UserError, message):
            section.name = 'Other'
        with self.assertRaisesRegex(UserError, message):
            self._section(live, 'New')

    # -- sections on live forms (FR-FVF-0003/4/6/7) ------------------------------------

    def _published_with_sections(self):
        layout = self._layout(self.company_be)
        accounting = self._section(layout, 'Accounting', 'closed')
        details = self._section(layout, 'Contact details')
        return layout, accounting, details

    def test_section_shows_after_original_content(self):
        """SC-FVF-0003-1 (FR-FVF-0003): the section "Accounting" with both fields follows the original content."""
        layout, accounting, _details = self._published_with_sections()
        self._place(layout, 'vat', accounting, 10)
        self._place(layout, 'website', accounting, 20)
        layout.action_publish()
        tree = self._arch(self.user_ben, self.company_be)
        node = self._section_node(tree, 'Accounting')
        self.assertIsNotNone(node)
        self.assertEqual(self._field_names(node), ['vat', 'website'])
        everything = [el for el in tree.iter()]
        original = tree.xpath('.//field[@name="name"][not(ancestor::field)]')[0]
        self.assertLess(everything.index(original), everything.index(node))

    def test_section_without_visible_field_is_not_shown(self):
        """SC-FVF-0003-2 (FR-FVF-0003): Anna, who cannot see Tax ID, does not get the section holding only it."""
        layout = self._layout(self.company_be)
        accounting = self._section(layout, 'Accounting')
        self._place(layout, 'vat', accounting)
        self._hide(layout, 'vat', self.group_a)
        layout.action_publish()
        self.assertIsNone(self._section_node(self._arch(self.user_anna, self.company_be), 'Accounting'))
        self.assertIsNotNone(self._section_node(self._arch(self.user_ben, self.company_be), 'Accounting'))

    def test_sections_start_as_set(self):
        """SC-FVF-0004-1 (FR-FVF-0004): Accounting starts closed, Contact details starts open."""
        layout, accounting, details = self._published_with_sections()
        self._place(layout, 'vat', accounting)
        self._place(layout, 'phone', details)
        layout.action_publish()
        tree = self._arch(self.user_ben, self.company_be)
        self.assertEqual(self._section_node(tree, 'Accounting').get('start_closed'), '1')
        self.assertEqual(self._section_node(tree, 'Contact details').get('start_closed'), '0')

    def test_new_section_starts_open(self):
        """SC-FVF-0004-2 (FR-FVF-0004): a section added without changing its start state starts open."""
        layout = self._layout(self.company_be)
        notes = self._section(layout, 'Notes')
        self.assertEqual(notes.start_state, 'open')
        self._place(layout, 'phone', notes)
        layout.action_publish()
        node = self._section_node(self._arch(self.user_ben, self.company_be), 'Notes')
        self.assertEqual(node.get('start_closed'), '0')

    def test_reordered_draft_shows_new_order_once_published(self):
        """SC-FVF-0006-1 (FR-FVF-0006): moving "website" above "vat" and publishing shows the new order."""
        layout = self._layout(self.company_be)
        accounting = self._section(layout, 'Accounting')
        self._place(layout, 'vat', accounting, 20)
        self._place(layout, 'website', accounting, 10)
        layout.action_publish()
        tree = self._arch(self.user_ben, self.company_be)
        self.assertEqual(self._field_names(self._section_node(tree, 'Accounting')), ['website', 'vat'])
        self.assertTrue(tree.xpath('.//field[@name="name"][not(ancestor::div[contains(@class, "%s")])]' % SECTION))

    def test_moved_field_shows_once_in_the_new_section(self):
        """SC-FVF-0007-1 (FR-FVF-0007): phone moved to Accounting shows once, there, not in Contact details."""
        layout, accounting, details = self._published_with_sections()
        self._place(layout, 'phone', accounting)
        self._place(layout, 'email', details)
        layout.action_publish()
        tree = self._arch(self.user_ben, self.company_be)
        self.assertEqual(len(tree.xpath('.//field[@name="phone"][not(ancestor::field)]')), 1)
        self.assertIn('phone', self._field_names(self._section_node(tree, 'Accounting')))
        self.assertNotIn('phone', self._field_names(self._section_node(tree, 'Contact details')))

    def test_field_out_of_all_sections_returns_to_original_place(self):
        """SC-FVF-0007-2 (FR-FVF-0007): a field taken out of every section is not inside a section on the form."""
        layout = self._layout(self.company_be)
        accounting = self._section(layout, 'Accounting')
        self._place(layout, 'phone', accounting)
        self._place(layout, 'vat', accounting)
        self._line(layout, 'phone').section_id = False
        layout.action_publish()
        tree = self._arch(self.user_ben, self.company_be)
        phone = tree.xpath('.//field[@name="phone"][not(ancestor::field)]')
        self.assertEqual(len(phone), 1)
        self.assertFalse(phone[0].xpath('ancestor::div[contains(@class, "%s")]' % SECTION))
        self.assertIn('vat', self._field_names(self._section_node(tree, 'Accounting')))
