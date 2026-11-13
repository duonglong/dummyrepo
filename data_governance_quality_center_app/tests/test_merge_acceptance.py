import re

from odoo import Command
from odoo.exceptions import UserError
from odoo.tests import new_test_user

from .acceptance_m2b_helpers import M2BCase, ROLLBACK, call_first, dump


class TestMergeAcceptance(M2BCase):
    """Acceptance tests of T-018: merge keeping every link, refusals and rollback.

    A merge is previewed with the merge dialog (``data.quality.merge.wizard``, opened from the
    group's context as a manager) and confirmed with its button; the history is
    ``data.quality.merge``. The linked documents are the contact's bank accounts, messages,
    attachments and followers, because the module has no sales orders. A rollback is the merge
    history's button, called as a manager. A refusal is read from the whole preview-and-confirm
    sequence: the build may refuse when the preview opens or when it is confirmed.

    Requirements: BR-DGQ-0008, FR-DGQ-0043, FR-DGQ-0044, FR-DGQ-0045
    """

    def setUp(self):
        super().setUp()
        self.rule = self.contact_rule()
        self.acme = self.partner('Acme Ltd', email='info@acme.com')
        self.acme_two = self.partner('ACME Ltd.', email='INFO@acme.com')

    def group(self):
        return self.make_group(self.rule, self.acme | self.acme_two)

    def merge(self, group=None):
        """Preview and confirm the merge of the group as manager Ann; return the merge history."""
        group = group or self.group()
        wizard = self.open_preview(group=group, user=self.manager_a)
        self.confirm_merge(wizard)
        self.env.flush_all()
        group.invalidate_recordset()
        return self.merge_of(group)

    def refused(self, records, fragment=None, group=None):
        """Preview and confirm a merge of ``records``: it is refused with a message naming the reason."""
        with self.assertRaises(UserError) as caught:
            wizard = self.open_preview(group=group, records=records, user=self.manager_a)
            self.confirm_merge(wizard)
        if fragment:
            self.assertTrue(re.search(fragment, str(caught.exception), re.I), str(caught.exception))
        return caught.exception

    # ---- FR-DGQ-0043 -------------------------------------------------------

    def test_confirmed_merge_moves_every_link_and_records_the_history(self):
        """SC-DGQ-0043-1: the bank account and 2 messages of 'ACME Ltd.' belong to 'Acme Ltd'; it is archived; the 3 links are in the history."""
        bank = self.link_bank(self.acme_two, 'MERGE-1')
        self.acme_two.message_post(body='first note')
        self.acme_two.message_post(body='second note')
        messages = self.env['mail.message'].search(
            [('model', '=', 'res.partner'), ('res_id', '=', self.acme_two.id), ('body', 'ilike', 'note')])
        self.assertEqual(len(messages), 2)
        self.acme_two.phone = '555-0100'
        group = self.group()
        merge = self.merge(group)
        self.assertEqual(len(merge), 1)
        bank.invalidate_recordset()
        self.assertEqual(bank.partner_id, self.acme)
        messages.invalidate_recordset()
        self.assertEqual(set(messages.mapped('res_id')), {self.acme.id})
        self.acme_two.invalidate_recordset()
        self.assertFalse(self.acme_two.active)
        self.assertTrue(self.acme.active)
        self.assertEqual(self.acme.phone, '555-0100')
        self.assertEqual(merge.user_id, self.manager_a)
        self.assertTrue(merge.date)
        self.assertEqual(merge.kept_res_id, self.acme.id)
        self.assertIn('phone', dump(merge.values_before))
        moved = dump(merge.moved_links)
        for link in [bank.id] + messages.ids:
            self.assertTrue(re.search(r'\b%d\b' % link, moved), 'link %s missing in %s' % (link, moved))
        self.assertEqual(merge.state, 'done')
        self.assertEqual(group.state, 'merged')
        self.assertEqual(group.merge_id, merge)

    def test_attachments_and_followers_move_too(self):
        """FR-DGQ-0043: attachments and followers of the merged-away record point to the kept record."""
        attachment = self.env['ir.attachment'].create({
            'name': 'contract.txt', 'raw': b'x', 'res_model': 'res.partner', 'res_id': self.acme_two.id})
        follower = self.env['res.partner'].create({'name': 'Watcher', 'email': 'w@w.com'})
        self.acme_two.message_subscribe(partner_ids=follower.ids)
        self.merge()
        attachment.invalidate_recordset()
        self.assertEqual(attachment.res_id, self.acme.id)
        followers = self.env['mail.followers'].search(
            [('res_model', '=', 'res.partner'), ('res_id', '=', self.acme.id)]).mapped('partner_id')
        self.assertIn(follower, followers)
        self.assertFalse(self.env['mail.followers'].search_count(
            [('res_model', '=', 'res.partner'), ('res_id', '=', self.acme_two.id),
             ('partner_id', '=', follower.id)]))

    def test_a_failing_link_move_changes_nothing_and_shows_the_reason(self):
        """SC-DGQ-0043-2: moving one linked document fails (the kept record already has that account): nothing changes."""
        kept_bank = self.link_bank(self.acme, 'SAME-ACCOUNT')
        other_bank = self.link_bank(self.acme_two, 'SAME-ACCOUNT')
        self.acme_two.phone = '555-0100'
        group = self.group()
        wizard = self.open_preview(group=group, user=self.manager_a)
        with self.assertRaises(UserError) as caught:
            self.confirm_merge(wizard)
        self.assertTrue(str(caught.exception).strip())
        self.env.flush_all()
        for record in (self.acme, self.acme_two, kept_bank, other_bank, group):
            record.invalidate_recordset()
        self.assertTrue(self.acme_two.active)
        self.assertFalse(self.acme.phone)
        self.assertEqual(kept_bank.partner_id, self.acme)
        self.assertEqual(other_bank.partner_id, self.acme_two)
        self.assertEqual(group.state, 'open')
        self.assertFalse(self.merge_of(group))

    # ---- FR-DGQ-0044 -------------------------------------------------------

    def test_contacts_of_two_users_are_refused(self):
        """SC-DGQ-0044-1: two contacts each linked to a user account: refused, contacts of more than one user cannot be merged."""
        partners = self.env['res.partner']
        for number in (1, 2):
            user = new_test_user(self.env, login='dgq_m2b_dupuser%d' % number, groups='base.group_user')
            user.partner_id.write({
                'name': 'Dup User' if number == 1 else 'DUP USER', 'email': 'dup@user.com',
                'company_id': self.cx.id})
            partners |= user.partner_id
        self.refused(partners, r'user')
        for partner in partners:
            self.assertTrue(partner.active)

    def test_contacts_of_two_companies_are_refused(self):
        """FR-DGQ-0044: records of different companies are refused with a message naming the companies."""
        other = self.partner('Acme Ltd', self.cy, email='info@acme.com')
        self.refused(self.acme | other, r'compan')
        self.assertTrue(other.active)

    def test_a_record_changed_since_the_preview_is_refused(self):
        """SC-DGQ-0044-2: 'ACME Ltd.' edited by another user after the preview: the merge is refused, open the preview again."""
        group = self.group()
        wizard = self.open_preview(group=group, user=self.manager_a)
        with self.mock_datetime_and_now('2099-01-01 00:00:00'):
            self.acme_two.sudo().write({'phone': '555-0123'})
        self.env.flush_all()
        with self.assertRaises(UserError) as caught:
            self.confirm_merge(wizard)
        self.assertIn('preview', str(caught.exception).lower())
        self.acme_two.invalidate_recordset()
        self.assertTrue(self.acme_two.active)
        self.assertFalse(self.merge_of(group))

    def test_a_record_archived_since_the_preview_is_refused(self):
        """FR-DGQ-0044: a record archived since the preview makes the confirmation refuse."""
        group = self.group()
        wizard = self.open_preview(group=group, user=self.manager_a)
        self.acme_two.sudo().active = False
        with self.assertRaises(UserError):
            self.confirm_merge(wizard)
        self.assertFalse(self.merge_of(group))

    def test_a_company_contact_with_its_own_contact_is_refused(self):
        """FR-DGQ-0044: a company contact merged with one of its own contacts is refused."""
        child = self.partner('Acme Ltd', email='info@acme.com', parent_id=self.acme.id)
        self.refused(self.acme | child, r'own|child|contact')
        self.assertTrue(child.active)

    def test_a_protected_record_is_refused_without_authorization(self):
        """FR-DGQ-0044 / BR-DGQ-0005: records of a restricted model are refused without explicit authorization."""
        self.env['data.quality.model.config'].search([('model_name', '=', 'res.partner')]).write(
            {'status': 'restricted'})
        self.refused(self.acme | self.acme_two, r'protect|restrict|authori')
        self.assertTrue(self.acme_two.active)

    def test_products_with_several_variants_are_refused(self):
        """FR-DGQ-0044: a product with several variants cannot be merged."""
        size = self.env['product.attribute'].create({
            'name': 'DGQ Size',
            'value_ids': [Command.create({'name': 'S'}), Command.create({'name': 'L'})]})
        with_variants = self.product('Desk')
        with_variants.write({'attribute_line_ids': [Command.create({
            'attribute_id': size.id, 'value_ids': [Command.set(size.value_ids.ids)]})]})
        self.assertEqual(len(with_variants.product_variant_ids), 2)
        plain = self.product('Desk')
        self.refused(with_variants | plain, r'variant')
        self.assertTrue(plain.active)

    def test_products_with_different_units_are_refused(self):
        """FR-DGQ-0044: products with different units of measure cannot be merged."""
        unit = self.env.ref('uom.product_uom_unit')
        dozen = self.env.ref('uom.product_uom_dozen')
        one = self.product('Pen', uom_id=unit.id)
        two = self.product('Pen', uom_id=dozen.id)
        self.refused(one | two, r'unit|uom')
        self.assertTrue(two.active)

    # ---- BR-DGQ-0008 / FR-DGQ-0045 -----------------------------------------

    def test_rollback_restores_the_record_and_keeps_later_links(self):
        """SC-DGQ-0045-1: after the rollback 'ACME Ltd.' is active with its bank account, the new one stays on 'Acme Ltd', the merge shows rolled back."""
        bank = self.link_bank(self.acme_two, 'BACK-1')
        self.acme_two.phone = '555-0100'
        group = self.group()
        merge = self.merge(group)
        later = self.link_bank(self.acme, 'AFTER-MERGE')
        call_first(merge.with_user(self.manager_a), ROLLBACK)
        self.env.flush_all()
        for record in (self.acme, self.acme_two, bank, later, merge, group):
            record.invalidate_recordset()
        self.assertTrue(self.acme_two.active)
        self.assertEqual(bank.partner_id, self.acme_two)
        self.assertEqual(later.partner_id, self.acme)
        self.assertEqual(merge.state, 'rolled_back')
        self.assertEqual(merge.rollback_user_id, self.manager_a)
        self.assertTrue(merge.rollback_date)
        self.assertEqual(group.state, 'open')
        self.assertFalse(self.acme.phone, 'the value the merge wrote is restored')

    def test_a_field_changed_by_a_person_since_the_merge_is_left_and_listed(self):
        """FR-DGQ-0045: a field of the kept record changed since the merge keeps its value and is listed in the report."""
        self.acme_two.phone = '555-0100'
        merge = self.merge()
        self.acme.phone = '555-9999'
        call_first(merge.with_user(self.manager_a), ROLLBACK)
        self.acme.invalidate_recordset()
        self.assertEqual(self.acme.phone, '555-9999')
        merge.invalidate_recordset()
        self.assertIn('phone', dump(merge.rollback_report))

    def test_no_rollback_when_a_merged_away_record_was_deleted(self):
        """SC-DGQ-0045-2: the merged-away record was deleted afterwards: the rollback is refused with the reason."""
        merge = self.merge()
        self.acme_two.invalidate_recordset()
        self.acme_two.sudo().unlink()
        with self.assertRaises(UserError) as caught:
            call_first(merge.with_user(self.manager_a), ROLLBACK)
        self.assertTrue(re.search(r'exist|delet|archiv', str(caught.exception), re.I), str(caught.exception))
        merge.invalidate_recordset()
        self.assertEqual(merge.state, 'done')

    def test_a_merge_cannot_be_rolled_back_twice(self):
        """BR-DGQ-0008: a merge already rolled back offers no second rollback."""
        merge = self.merge()
        call_first(merge.with_user(self.manager_a), ROLLBACK)
        with self.assertRaises(UserError):
            call_first(merge.with_user(self.manager_a), ROLLBACK)
