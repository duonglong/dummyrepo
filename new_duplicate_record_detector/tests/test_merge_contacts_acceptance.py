from unittest.mock import patch

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError
from odoo.sql_db import Cursor
from odoo.tests.common import TransactionCase


class TestMergeContactsAcceptance(TransactionCase):
    """Acceptance tests of T-005 (contact merge).

    Requirements: BR-DRD-0007, FR-DRD-0010, FR-DRD-0011, FR-DRD-0012, FR-DRD-0013,
    FR-DRD-0014, FR-DRD-0015.

    The wizard is started with ``active_model`` / ``active_ids`` (or ``active_model=
    'duplicate.group'`` and ``active_id``) in the context and confirmed with ``action_merge``.
    Bank accounts, tags, messages, followers and attachments stand for linked documents.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        base = cls.env.ref('base.group_user') | cls.env.ref('base.group_partner_manager')
        mgr_group = cls.env.ref('new_duplicate_record_detector.group_duplicate_manager')
        cls.manager = cls.env['res.users'].create({
            'name': 'Dup Mgr T005', 'login': 'dup_mgr_t005',
            'group_ids': [Command.set((base | mgr_group).ids)],
        })
        cls.other_manager = cls.env['res.users'].create({
            'name': 'Other Mgr T005', 'login': 'other_mgr_t005',
            'group_ids': [Command.set((base | mgr_group).ids)],
        })
        cls.sales = cls.env['res.users'].create({
            'name': 'Sales T005', 'login': 'sales_t005',
            'group_ids': [Command.set(base.ids)],
        })
        cls.P = cls.env['res.partner']

    # helpers
    def _banks(self, partner, count, tag='T005'):
        for i in range(count):
            self.env['res.partner.bank'].create({
                'account_number': '%s-%s-%s' % (tag, partner.id, i), 'partner_id': partner.id})

    def _pair(self):
        self.acme = self.P.create({'name': 'Acme', 'email': 'info@acme.com'})
        self.acme_ltd = self.P.create({
            'name': 'ACME Ltd', 'email': 'sales@acme.com', 'phone': '555-0100'})
        return self.acme, self.acme_ltd

    def _start(self, ids, user=None):
        wizard = self.env['duplicate.merge.wizard'].with_user(user or self.manager)
        return wizard.with_context(active_model='res.partner', active_ids=list(ids)).create({})

    def _start_group(self, group, user=None):
        wizard = self.env['duplicate.merge.wizard'].with_user(user or self.manager)
        return wizard.with_context(
            active_model='duplicate.group', active_id=group.id,
            active_ids=group.ids).create({})

    def _merge(self, ids, user=None):
        wizard = self._start(ids, user=user)
        wizard.action_merge()
        return wizard

    def _set(self, partners):
        rule = self.env['duplicate.rule'].sudo().create({
            'name': 'T005 rule',
            'model_id': self.env['ir.model']._get('res.partner').id,
            'condition_ids': [Command.create({
                'field_id': self.env['ir.model.fields']._get('res.partner', 'vat').id,
                'match_type': 'exact'})],
        })
        return self.env['duplicate.group'].sudo().create({
            'rule_id': rule.id, 'model_name': 'res.partner',
            'line_ids': [Command.create({
                'res_model': 'res.partner', 'res_id': p.id, 'name': p.display_name})
                for p in partners],
        })

    def _refresh(self):
        self.env.flush_all()
        self.env.invalidate_all()

    def _count(self, model, domain):
        return self.env[model].sudo().with_context(active_test=False).search_count(domain)

    def _msgs(self, partner, body):
        return self._count('mail.message', [
            ('model', '=', 'res.partner'), ('res_id', '=', partner.id),
            ('body', 'ilike', body)])

    # SC-DRD-0011-1
    def test_sc_0011_1_every_link_moves_to_the_kept_record(self):
        """Requirements: FR-DRD-0011. Scenario SC-DRD-0011-1."""
        acme, ltd = self._pair()
        self._banks(ltd, 3)
        ltd.message_post(body='T005 first message')
        ltd.message_post(body='T005 second message')
        follower = self.env['res.partner'].create({'name': 'T005 follower'})
        ltd.message_subscribe(partner_ids=follower.ids)
        attachment = self.env['ir.attachment'].create({
            'name': 'T005.txt', 'raw': b'x', 'res_model': 'res.partner', 'res_id': ltd.id})
        self._merge([acme.id, ltd.id])
        self._refresh()
        self.assertEqual(len(acme.bank_ids), 3)
        self.assertFalse(ltd.bank_ids)
        self.assertEqual(self._msgs(acme, 'T005 first message'), 1)
        self.assertEqual(self._msgs(acme, 'T005 second message'), 1)
        self.assertEqual(self._msgs(ltd, 'T005 '), 0)
        self.assertEqual((attachment.res_model, attachment.res_id), ('res.partner', acme.id))
        self.assertEqual(self._count('mail.followers', [
            ('res_model', '=', 'res.partner'), ('res_id', '=', acme.id),
            ('partner_id', '=', follower.id)]), 1)
        self.assertEqual(self._count('mail.followers', [
            ('res_model', '=', 'res.partner'), ('res_id', '=', ltd.id)]), 0)

    def test_sc_0011_1_other_models_pointing_to_the_partner_move_too(self):
        """Requirements: FR-DRD-0011. A many2one of another model follows the merge."""
        acme, ltd = self._pair()
        child = self.P.create({'name': 'T005 child', 'parent_id': ltd.id})
        self._merge([acme.id, ltd.id])
        self._refresh()
        self.assertEqual(child.parent_id, acme)

    # SC-DRD-0011-2
    def test_sc_0011_2_shared_tag_is_kept_once(self):
        """Requirements: FR-DRD-0011. Scenario SC-DRD-0011-2."""
        acme, ltd = self._pair()
        vip = self.env['res.partner.category'].create({'name': 'VIP T005'})
        other = self.env['res.partner.category'].create({'name': 'Only dup T005'})
        acme.category_id = vip
        ltd.category_id = vip | other
        self._merge([acme.id, ltd.id])
        self._refresh()
        self.env.cr.execute(
            "SELECT category_id FROM res_partner_res_partner_category_rel WHERE partner_id = %s",
            [acme.id])
        rows = [r[0] for r in self.env.cr.fetchall()]
        self.assertEqual(sorted(rows), sorted([vip.id, other.id]))
        self.assertEqual(acme.category_id.filtered(lambda c: c.name == 'VIP T005').mapped('id'),
                         [vip.id])

    # SC-DRD-0012-1
    def test_sc_0012_1_empty_fields_filled_and_filled_fields_kept(self):
        """Requirements: FR-DRD-0012. Scenario SC-DRD-0012-1."""
        acme, ltd = self._pair()
        self._merge([acme.id, ltd.id])
        self._refresh()
        self.assertEqual(acme.email, 'info@acme.com')
        self.assertEqual(acme.phone, '555-0100')

    def test_sc_0012_1_nothing_is_written_on_the_merged_away_record_values(self):
        """Requirements: FR-DRD-0012. The merged-away record keeps its own values."""
        acme, ltd = self._pair()
        self._merge([acme.id, ltd.id])
        self._refresh()
        self.assertEqual(ltd.email, 'sales@acme.com')

    # SC-DRD-0013-1 and BR-DRD-0007
    def test_sc_0013_1_merged_away_archived_set_merged_note_posted(self):
        """Requirements: FR-DRD-0013, BR-DRD-0007. Scenario SC-DRD-0013-1."""
        acme, ltd = self._pair()
        group = self._set(acme | ltd)
        mark = self.env['mail.message'].sudo().search([], order='id desc', limit=1).id or 0
        wizard = self._start_group(group)
        wizard.action_merge()
        self._refresh()
        self.assertTrue(acme.active)
        self.assertFalse(ltd.active)
        self.assertTrue(ltd.exists(), "the merged-away record must still exist")
        self.assertEqual(group.state, 'merged')
        self.assertEqual(group.kept_res_id, acme.id)
        self.assertEqual(group.kept_name, 'Acme')
        self.assertEqual(group.merge_user_id, self.manager)
        self.assertTrue(group.merge_date)
        notes = self.env['mail.message'].sudo().search([
            ('model', '=', 'res.partner'), ('res_id', '=', acme.id), ('id', '>', mark),
            ('body', 'ilike', 'ACME Ltd')])
        self.assertEqual(len(notes), 1, "one note on the kept record naming the merged-away one")
        self.assertIn('Dup Mgr T005', notes.body)
        self.assertIn(str(fields.Date.today().year), notes.body)
        self.assertEqual(notes.subtype_id, self.env.ref('mail.mt_note'))

    def test_partial_merge_opens_a_new_set_with_the_kept_record(self):
        """Requirements: FR-DRD-0013. Records left out stay on the list with the kept one."""
        acme, ltd = self._pair()
        third = self.P.create({'name': 'Acme Trading'})
        group = self._set(acme | ltd | third)
        wizard = self._start_group(group)
        wizard.record_line_ids.filtered(lambda l: l.res_id == third.id).include = False
        wizard.action_merge()
        self._refresh()
        self.assertEqual(group.state, 'merged')
        self.assertFalse(ltd.active)
        self.assertTrue(third.active)
        new = self.env['duplicate.group'].sudo().search([
            ('rule_id', '=', group.rule_id.id), ('state', '=', 'open')])
        self.assertEqual(len(new), 1)
        self.assertEqual(set(new.line_ids.mapped('res_id')), {acme.id, third.id})

    # SC-DRD-0010-1
    def test_sc_0010_1_refused_when_a_record_was_archived_after_the_preview(self):
        """Requirements: FR-DRD-0010. Scenario SC-DRD-0010-1."""
        acme, ltd = self._pair()
        self._banks(ltd, 3)
        wizard = self._start([acme.id, ltd.id])
        ltd.with_user(self.other_manager).write({'active': False})
        with self.assertRaisesRegex(UserError, '(?i)preview'):
            wizard.action_merge()
        self._refresh()
        self.assertTrue(acme.active)
        self.assertFalse(acme.phone)
        self.assertEqual(len(acme.bank_ids), 0)
        self.assertEqual(len(ltd.bank_ids), 3)

    def test_sc_0010_1_refused_when_a_record_was_deleted_after_the_preview(self):
        """Requirements: FR-DRD-0010. A deleted record also refuses the merge."""
        acme, ltd = self._pair()
        wizard = self._start([acme.id, ltd.id])
        ltd.sudo().unlink()
        with self.assertRaisesRegex(UserError, '(?i)preview'):
            wizard.action_merge()
        self._refresh()
        self.assertTrue(acme.active)
        self.assertFalse(acme.phone)

    # SC-DRD-0014-1
    def test_sc_0014_1_failure_while_moving_a_link_undoes_the_whole_merge(self):
        """Requirements: FR-DRD-0014. Scenario SC-DRD-0014-1."""
        acme, ltd = self._pair()
        self._banks(ltd, 3)
        ltd.message_post(body='T005 kept message')
        vip = self.env['res.partner.category'].create({'name': 'VIP T005b'})
        ltd.category_id = vip
        group = self._set(acme | ltd)
        wizard = self._start_group(group)
        original_execute = Cursor.execute

        def failing_execute(cr, query, *args, **kwargs):
            code = str(getattr(query, 'code', query))
            if 'UPDATE' in code.upper() and 'res_partner_bank' in code:
                raise ValueError('boom-t005')
            return original_execute(cr, query, *args, **kwargs)

        with patch.object(Cursor, 'execute', failing_execute):
            with self.assertRaisesRegex(UserError, 'boom-t005'):
                wizard.action_merge()
        self._refresh()
        self.assertTrue(acme.active and ltd.active)
        self.assertEqual(len(ltd.bank_ids), 3)
        self.assertEqual(len(acme.bank_ids), 0)
        self.assertEqual(self._msgs(ltd, 'T005 kept message'), 1)
        self.assertEqual(self._msgs(acme, 'T005 kept message'), 0)
        self.assertEqual(ltd.category_id, vip)
        self.assertFalse(acme.category_id)
        self.assertFalse(acme.phone)
        self.assertEqual(group.state, 'open')

    # SC-DRD-0015-1
    def test_sc_0015_1_company_and_its_own_contact_refused(self):
        """Requirements: FR-DRD-0015. Scenario SC-DRD-0015-1."""
        company = self.P.create({'name': 'Acme', 'is_company': True})
        contact = self.P.create({'name': 'John of Acme', 'parent_id': company.id})
        with self.assertRaisesRegex(UserError, '(?i)company.*contact|contact.*company'):
            self._merge([company.id, contact.id])
        self._refresh()
        self.assertTrue(company.active and contact.active)
        self.assertEqual(contact.parent_id, company)

    # SC-DRD-0015-2
    def test_sc_0015_2_contacts_of_two_users_refused(self):
        """Requirements: FR-DRD-0015. Scenario SC-DRD-0015-2."""
        user_a = self.env['res.users'].create({'name': 'User A T005', 'login': 'user_a_t005'})
        user_b = self.env['res.users'].create({'name': 'User B T005', 'login': 'user_b_t005'})
        with self.assertRaisesRegex(UserError, '(?i)more than one user'):
            self._merge([user_a.partner_id.id, user_b.partner_id.id])
        self._refresh()
        self.assertTrue(user_a.partner_id.active and user_b.partner_id.active)
        self.assertEqual(user_a.partner_id.user_ids, user_a)

    # FR-DRD-0017 at confirm time
    def test_non_manager_cannot_confirm_a_merge(self):
        """Requirements: FR-DRD-0017. A merge confirmed by a non-manager is refused."""
        acme, ltd = self._pair()
        wizard = self._start([acme.id, ltd.id])
        with self.assertRaises(AccessError):
            wizard.with_user(self.sales).action_merge()
        self._refresh()
        self.assertTrue(acme.active and ltd.active)
        self.assertFalse(acme.phone)
