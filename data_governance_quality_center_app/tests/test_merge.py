from unittest.mock import patch

from odoo import Command
from odoo.exceptions import AccessError, UserError
from odoo.tests import Form

from ..services import merge as merge_service
from .acceptance_m2b_helpers import M2BCase, ROLLBACK, call_first, dump


class TestMerge(M2BCase):
    """The merge keeps every link, refuses unsafe merges and rolls back.

    Requirements: FR-DGQ-0043, FR-DGQ-0044, FR-DGQ-0045, BR-DGQ-0008
    """

    def setUp(self):
        super().setUp()
        self.rule = self.contact_rule()
        self.acme = self.partner('Acme Ltd', email='info@acme.com')
        self.acme_two = self.partner('ACME Ltd.', email='INFO@acme.com')

    def preview(self, kept, group=None, user=None):
        """The merge preview with ``kept`` chosen, saved as the manager does in the dialog."""
        group = group or self.make_group(self.rule, self.acme | self.acme_two)
        Wizard = self.env['data.quality.merge.wizard'].with_user(user or self.manager_a)
        context = {
            'default_group_id': group.id, 'default_res_model': 'res.partner',
            'default_res_ids': sorted(self.group_ids(group))}
        with Form(Wizard.with_context(**context)) as form:
            form.kept_res_id = kept.id
        return form.record

    def merge_into(self, kept):
        wizard = self.preview(kept)
        wizard.action_confirm()
        self.env.flush_all()
        return self.merge_of(wizard.group_id)

    def test_every_link_moves_to_the_record_kept_and_is_recorded(self):
        """FR-DGQ-0043: bank account, messages and attachment move to the kept record, and the history lists them."""
        bank = self.link_bank(self.acme_two, 'MV-1')
        self.acme_two.message_post(body='note one')
        attachment = self.env['ir.attachment'].create({
            'name': 'a.txt', 'raw': b'x', 'res_model': 'res.partner', 'res_id': self.acme_two.id})
        self.acme_two.phone = '555-0100'
        merge = self.merge_into(self.acme)
        bank.invalidate_recordset()
        attachment.invalidate_recordset()
        self.assertEqual(bank.partner_id, self.acme)
        self.assertEqual(attachment.res_id, self.acme.id)
        self.assertEqual(self.acme.phone, '555-0100')
        self.assertFalse(self.acme_two.active)
        self.assertEqual(merge.user_id, self.manager_a)
        self.assertEqual(merge.state, 'done')
        self.assertTrue(merge.reversible)
        moved = dump(merge.moved_links)
        self.assertIn(str(bank.id), moved)
        self.assertIn(str(attachment.id), moved)
        self.assertEqual(merge.group_id.state, 'merged')

    def test_a_follower_the_kept_record_already_has_stays_where_it_is(self):
        """FR-DGQ-0043: a follower of both records does not stop the merge."""
        watcher = self.env['res.partner'].create({'name': 'Watcher DGQ'})
        self.acme.message_subscribe(partner_ids=watcher.ids)
        self.acme_two.message_subscribe(partner_ids=watcher.ids)
        self.merge_into(self.acme)
        followers = self.env['mail.followers'].search(
            [('res_model', '=', 'res.partner'), ('res_id', '=', self.acme.id), ('partner_id', '=', watcher.id)])
        self.assertEqual(len(followers), 1)

    def test_a_tag_both_records_have_does_not_stop_the_merge(self):
        """FR-DGQ-0043: a link table row the kept record already has is not a unique conflict."""
        tag = self.env['res.partner.category'].create({'name': 'DGQ tag'})
        (self.acme | self.acme_two).write({'category_id': [Command.link(tag.id)]})
        self.merge_into(self.acme)
        self.assertEqual(self.acme.category_id, tag)

    def test_the_merge_is_refused_once_the_group_is_closed(self):
        """FR-DGQ-0043: a merged group cannot be merged again."""
        wizard = self.preview(self.acme)
        wizard.action_confirm()
        with self.assertRaises(UserError):
            wizard.action_confirm()

    def test_only_a_manager_confirms(self):
        """FR-DGQ-0043: a viewer who reaches the dialog cannot confirm."""
        wizard = self.preview(self.acme)
        viewer = self.user('viewer', login='dgq_m2b_view', company=self.cx)
        with self.assertRaises(UserError):
            wizard.with_user(viewer).action_confirm()
        self.assertTrue(self.acme_two.active)

    def test_a_protected_model_needs_the_authorization_of_an_administrator(self):
        """FR-DGQ-0044: a restricted model merges only with the authorization of an administrator."""
        self.env['data.quality.model.config'].search([('model_name', '=', 'res.partner')]).write(
            {'status': 'restricted'})
        wizard = self.preview(self.acme)
        wizard.protected_authorized = True
        with self.assertRaises(UserError):
            wizard.action_confirm()
        wizard.with_user(self.administrator).action_confirm()
        self.assertFalse(self.acme_two.active)

    def test_a_strategy_can_declare_its_model_irreversible(self):
        """BR-DGQ-0008: the preview and the history show a merge irreversible and no rollback is offered."""
        strategy = {'key': 'dgq.test', 'label': 'Test', 'model': 'res.partner', 'reversible': False,
                    'handler': lambda env, records: None}
        registry = type(self.env['data.quality.registry'])
        with patch.object(registry, '_dgq_merge_strategies', lambda self: [strategy]):
            wizard = self.preview(self.acme)
            self.assertFalse(wizard.reversible)
            wizard.action_confirm()
            merge = self.merge_of(wizard.group_id)
            self.assertFalse(merge.reversible)
            self.assertTrue(merge.rollback_blocked)
            with self.assertRaises(UserError):
                call_first(merge.with_user(self.manager_a), ROLLBACK)

    def test_the_records_come_back_with_their_links_after_a_rollback(self):
        """FR-DGQ-0045: a rolled back merge points the moved attachment back and reopens the group."""
        attachment = self.env['ir.attachment'].create({
            'name': 'b.txt', 'raw': b'x', 'res_model': 'res.partner', 'res_id': self.acme_two.id})
        merge = self.merge_into(self.acme)
        self.assertFalse(merge.rollback_blocked)
        call_first(merge.with_user(self.manager_a), ROLLBACK)
        attachment.invalidate_recordset()
        self.assertEqual(attachment.res_id, self.acme_two.id)
        self.assertTrue(self.acme_two.active)
        self.assertEqual(merge.group_id.state, 'open')
        self.assertTrue(merge.rollback_blocked)

    def test_single_variant_products_merge_and_their_variant_links_move(self):
        """FR-DGQ-0043: product templates with one variant each merge and the template is archived."""
        one = self.product('Pen DGQ', barcode=False)
        two = self.product('Pen DGQ')
        rule = self.dup_rule('product.template', [('name', 'exact', 1)], threshold=85)
        group = self.make_group(rule, one | two)
        Wizard = self.env['data.quality.merge.wizard'].with_user(self.manager_a)
        context = {
            'default_group_id': group.id, 'default_res_model': 'product.template',
            'default_res_ids': sorted(self.group_ids(group))}
        with Form(Wizard.with_context(**context)) as form:
            form.kept_res_id = one.id
        form.record.action_confirm()
        self.assertFalse(two.active)
        self.assertTrue(one.active)

    def test_a_link_table_that_cannot_be_recorded_shows_its_reason(self):
        """FR-DGQ-0043: an unrecordable link table is a reason shown to the person and nothing changes."""
        wizard = self.preview(self.acme)
        with patch.object(merge_service, 'move_links', side_effect=merge_service.UnrecordableLinks('some_rel')):
            with self.assertRaisesRegex(UserError, 'some_rel'):
                wizard.action_confirm()
        self.assertTrue(self.acme_two.active)

    def test_an_edit_made_while_the_dialog_is_open_refuses_the_merge(self):
        """FR-DGQ-0044: the snapshot is taken when the dialog opens (action_merge), as the unsaved dialog needs.

        The dialog is edited elsewhere before its record is saved on the Merge click; the button press itself is not run here.
        """
        group = self.make_group(self.rule, self.acme | self.acme_two)
        action = group.with_user(self.manager_a).action_merge()
        with Form(self.env['data.quality.merge.wizard'].with_user(self.manager_a).with_context(
                **action['context'])) as form:
            form.kept_res_id = self.acme.id
            self.acme_two.sudo().active = False
            self.env.flush_all()
        with self.assertRaisesRegex(UserError, 'changed since the preview'):
            form.record.action_confirm()
        self.assertTrue(self.acme.active)

    def test_the_image_of_a_binary_field_is_not_moved(self):
        """FR-DGQ-0043: the image of the merged-away record stays with it; only document attachments move."""
        image = self.env['ir.attachment'].create({
            'name': 'image_1920', 'res_model': 'res.partner', 'res_field': 'image_1920',
            'res_id': self.acme_two.id, 'raw': b'img'})
        self.merge_into(self.acme)
        image.invalidate_recordset()
        self.assertEqual(image.res_id, self.acme_two.id)

    def test_a_viewer_cannot_read_the_merge_history(self):
        """BR-DGQ-0008: the merge history belongs to managers, not to viewers."""
        merge = self.merge_into(self.acme)
        viewer = self.user('viewer', login='dgq_m2b_view2', company=self.cx)
        with self.assertRaises(AccessError):
            merge.with_user(viewer).check_access('read')
