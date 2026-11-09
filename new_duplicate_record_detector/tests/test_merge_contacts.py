from unittest.mock import patch

from odoo import Command, fields
from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase


class TestMergeContacts(TransactionCase):
    """Own tests of the contact merge (T-005).

    Requirements: FR-DRD-0010, FR-DRD-0012, FR-DRD-0013, FR-DRD-0014, BR-DRD-0007.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        groups = cls.env.ref('base.group_user') | cls.env.ref('base.group_partner_manager') | cls.env.ref(
            'new_duplicate_record_detector.group_duplicate_manager')
        cls.manager = cls.env['res.users'].create({
            'name': 'Merge Mgr', 'login': 'merge_mgr_t005u', 'group_ids': [Command.set(groups.ids)]})
        cls.acme = cls.env['res.partner'].create({'name': 'Acme', 'phone': '111'})
        cls.ltd = cls.env['res.partner'].create({'name': 'ACME Ltd', 'phone': '222', 'website': 'acme.test'})

    def _wizard(self, partners):
        return self.env['duplicate.merge.wizard'].with_user(self.manager).with_context(
            active_model='res.partner', active_ids=partners.ids).create({})

    def test_filled_field_is_never_overwritten(self):
        """Requirements: FR-DRD-0012. A value that exists on the kept record stays, even if the
        record was filled after the preview was computed."""
        wizard = self._wizard(self.acme | self.ltd)
        self.acme.website = 'https://kept.test'
        wizard.action_merge()
        self.env.invalidate_all()
        self.assertEqual(self.acme.phone, '111')
        self.assertEqual(self.acme.website, 'https://kept.test')

    def test_merged_without_set_archives_and_notes(self):
        """Requirements: FR-DRD-0013, BR-DRD-0007. A list selection has no set to mark."""
        wizard = self._wizard(self.acme | self.ltd)
        wizard.action_merge()
        self.env.invalidate_all()
        self.assertFalse(self.ltd.active)
        self.assertTrue(self.ltd.exists())
        self.assertIn('ACME Ltd', self.acme.message_ids[0].body)

    def test_unticked_kept_record_is_refused(self):
        """Requirements: FR-DRD-0010. The merge re-checks the selection when confirmed."""
        wizard = self._wizard(self.acme | self.ltd)
        wizard.record_line_ids.filtered(lambda line: line.res_id == wizard.kept_res_id).include = False
        with self.assertRaises(UserError):
            wizard.action_merge()
        self.assertTrue(self.acme.active and self.ltd.active)

    def test_failure_is_shown_and_nothing_changes(self):
        """Requirements: FR-DRD-0014. A step that raises undoes the archive and the fill."""
        wizard = self._wizard(self.acme | self.ltd)
        native = type(self.env['base.partner.merge.automatic.wizard'])
        with patch.object(native, '_update_reference_fields', side_effect=ValueError('step-broke')):
            with self.assertRaisesRegex(UserError, 'step-broke'):
                wizard.action_merge()
        self.env.invalidate_all()
        self.assertTrue(self.ltd.active)
        self.assertEqual(self.acme.phone, '111')

    def test_forged_value_line_is_ignored(self):
        """Requirements: FR-DRD-0012, FR-DRD-0017. A value line written by hand never reaches the
        kept contact: the values are rebuilt from the records."""
        other_company = self.env['res.company'].create({'name': 'Forged Co'})
        wizard = self._wizard(self.acme | self.ltd)
        self.env['duplicate.merge.value.line'].with_user(self.manager).create({
            'wizard_id': wizard.id, 'field_name': 'company_id', 'is_filled': True,
            'write_value': other_company.id})
        wizard.action_merge()
        self.env.invalidate_all()
        self.assertNotEqual(self.acme.company_id, other_company)

    def test_unreadable_record_is_refused(self):
        """Requirements: FR-DRD-0017. A record line for a contact the manager cannot read is refused."""
        wizard = self._wizard(self.acme | self.ltd)
        hidden = self.env['res.partner'].create({
            'name': 'Hidden', 'company_id': self.env['res.company'].create({'name': 'Hidden Co'}).id})
        self.env['duplicate.merge.record.line'].with_user(self.manager).create({
            'wizard_id': wizard.id, 'res_id': hidden.id, 'name': 'Hidden'})
        with self.assertRaises(UserError):
            wizard.action_merge()
        self.assertTrue(hidden.active and self.ltd.active)

    def test_activities_move_to_the_kept_contact(self):
        """Requirements: FR-DRD-0011. An activity of the merged-away contact follows the merge."""
        activity = self.ltd.activity_schedule(act_type_xmlid='mail.mail_activity_data_todo', summary='Call')
        self._wizard(self.acme | self.ltd).action_merge()
        self.env.invalidate_all()
        self.assertEqual(activity.res_id, self.acme.id)


class TestMergeOtherOpenSets(TransactionCase):
    """Regression tests for B-0137: a merge cleans every open set holding a merged-away record.

    Requirements: FR-DRD-0007, FR-DRD-0013, BR-DRD-0004.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        groups = cls.env.ref('base.group_user') | cls.env.ref('base.group_partner_manager') | cls.env.ref(
            'new_duplicate_record_detector.group_duplicate_manager')
        cls.manager = cls.env['res.users'].create({
            'name': 'Sets Mgr', 'login': 'sets_mgr_b0137', 'group_ids': [Command.set(groups.ids)]})
        cls.x = cls.env['res.partner'].create({'name': 'Xavier Co'})
        cls.y = cls.env['res.partner'].create({'name': 'Xavier Co '})
        cls.z = cls.env['res.partner'].create({'name': 'Xavier Co  '})
        cls.rule_email = cls._rule('Same e-mail', 'email')
        cls.rule_name = cls._rule('Same name', 'name')

    @classmethod
    def _rule(cls, name, field_name):
        return cls.env['duplicate.rule'].create({
            'name': name, 'model_id': cls.env['ir.model']._get('res.partner').id,
            'condition_ids': [Command.create({
                'field_id': cls.env['ir.model.fields']._get('res.partner', field_name).id,
                'match_type': 'exact'})]})

    def _open_set(self, rule, partners):
        return self.env['duplicate.group'].create({
            'rule_id': rule.id, 'model_name': 'res.partner',
            'line_ids': [Command.create({
                'res_model': 'res.partner', 'res_id': p.id, 'name': p.display_name})
                for p in partners]})

    def _merge(self, partners, group=None):
        context = {'active_model': 'res.partner', 'active_ids': partners.ids}
        if group:
            context = {'active_model': 'duplicate.group', 'active_id': group.id, 'active_ids': group.ids}
        self.env['duplicate.merge.wizard'].with_user(self.manager).with_context(**context).create({}).action_merge()
        self.env.invalidate_all()

    def test_bug_b_0137_merge_from_a_set_cleans_the_sets_of_other_rules(self):
        """Regression test for B-0137: the other rule's set {X, Y} drops Y and closes as merged.

        Requirements: FR-DRD-0013, BR-DRD-0004
        """
        by_email = self._open_set(self.rule_email, self.x | self.y)
        by_name = self._open_set(self.rule_name, self.x | self.y)
        self._merge(self.x | self.y, by_email)
        self.assertEqual(by_email.state, 'merged')
        self.assertEqual(by_name.state, 'merged')
        self.assertNotIn(self.y.id, by_name.line_ids.mapped('res_id'))

    def test_bug_b_0137_merge_from_a_list_repoints_open_sets(self):
        """Regression test for B-0137: a merge from a list selection re-points the merged-away
        record to the kept one in an open set that also holds a third record.

        Requirements: FR-DRD-0013, BR-DRD-0004
        """
        by_name = self._open_set(self.rule_name, self.y | self.z)
        self._merge(self.x | self.y)
        self.assertEqual(by_name.state, 'open')
        self.assertCountEqual(by_name.line_ids.mapped('res_id'), [self.x.id, self.z.id])
        self.assertEqual(by_name.record_count, 2)
        self.assertNotIn('Xavier Co ,', by_name.record_names)

    def test_bug_b_0140_left_out_record_is_reported_again_by_the_next_full_check(self):
        """Regression test for B-0140: a record left out of a merge is not part of the merged set,
        so the next full check still pairs it with the kept record.

        Requirements: FR-DRD-0005, FR-DRD-0006, FR-DRD-0013
        """
        group = self._open_set(self.rule_name, self.x | self.y | self.z)
        wizard = self.env['duplicate.merge.wizard'].with_user(self.manager).with_context(
            active_model='duplicate.group', active_id=group.id, active_ids=group.ids).create({})
        wizard.record_line_ids.filtered(lambda line: line.res_id == self.z.id).include = False
        wizard.action_merge()
        self.env.invalidate_all()
        self.assertNotIn(self.z.id, group.line_ids.mapped('res_id'))
        self.env['duplicate.full.check'].replace_open_sets(self.rule_name)
        open_sets = self.env['duplicate.group'].sudo().search([
            ('rule_id', '=', self.rule_name.id), ('state', '=', 'open')])
        self.assertEqual(len(open_sets), 1)
        self.assertEqual(set(open_sets.line_ids.mapped('res_id')), {self.x.id, self.z.id})

    def test_bug_b_0160_merge_with_records_in_several_open_sets(self):
        """Regression test for B-0160: Confirm merge works when the records sit in several open sets.

        Requirements: FR-DRD-0011, FR-DRD-0014
        """
        rule_phone = self._rule('Same phone', 'phone')
        by_name = self._open_set(self.rule_name, self.x | self.y)
        by_email = self._open_set(self.rule_email, self.x | self.y)
        by_phone = self._open_set(rule_phone, self.x | self.y)
        self._merge(self.x | self.y, by_name)
        for group in by_name | by_email | by_phone:
            self.assertEqual(group.state, 'merged')
        self.assertFalse(self.y.active)

    def test_bug_b_0148_left_out_contact_is_not_put_beside_its_own_company(self):
        """Regression test for B-0148: a contact left out of a merge stays out of the new open set
        when the kept record is its own company.

        Requirements: FR-DRD-0013, BR-DRD-0004
        """
        company = self.env['res.partner'].create({'name': 'Acme B0148', 'is_company': True})
        outside = self.env['res.partner'].create({'name': 'ACME Ltd B0148', 'is_company': True})
        contact = self.env['res.partner'].create({'name': 'John B0148', 'parent_id': company.id})
        group = self._open_set(self.rule_name, company | outside | contact)
        wizard = self.env['duplicate.merge.wizard'].with_user(self.manager).with_context(
            active_model='duplicate.group', active_id=group.id, active_ids=group.ids).create({})
        wizard.record_line_ids.filtered(lambda line: line.res_id == contact.id).include = False
        wizard.action_merge()
        self.env.invalidate_all()
        open_sets = self.env['duplicate.group'].sudo().search([
            ('rule_id', '=', self.rule_name.id), ('state', '=', 'open')])
        for members in open_sets.mapped(lambda g: set(g.line_ids.mapped('res_id'))):
            self.assertFalse({company.id, contact.id} <= members)

    def test_bug_b_0148_kept_company_does_not_replace_a_record_beside_its_own_contact(self):
        """Regression test for B-0148: in another open set, the kept company does not take the
        place of a merged-away record when the set holds one of its own contacts.

        Requirements: FR-DRD-0013, BR-DRD-0004
        """
        company = self.env['res.partner'].create({'name': 'Acme B0148', 'is_company': True})
        outside = self.env['res.partner'].create({'name': 'ACME Ltd B0148', 'is_company': True})
        contact = self.env['res.partner'].create({'name': 'John B0148', 'parent_id': company.id})
        by_name = self._open_set(self.rule_name, outside | contact)
        self._merge(company | outside)
        self.assertNotIn(company.id, by_name.line_ids.mapped('res_id'))


class TestMergeKeptRecordScope(TransactionCase):
    """Regression tests for B-0142 and B-0143: what the merge fills and what it moves.

    Requirements: FR-DRD-0009, FR-DRD-0011, FR-DRD-0012, BR-DRD-0004.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        groups = cls.env.ref('base.group_user') | cls.env.ref('base.group_partner_manager') | cls.env.ref(
            'new_duplicate_record_detector.group_duplicate_manager')
        cls.manager = cls.env['res.users'].create({
            'name': 'Scope Mgr', 'login': 'scope_mgr_b0142', 'group_ids': [Command.set(groups.ids)]})

    def _wizard(self, records):
        return self.env['duplicate.merge.wizard'].with_user(self.manager).with_context(
            active_model=records._name, active_ids=records.ids).create({})

    def _schedule_note(self, record):
        return self.env['mail.scheduled.message'].create({
            'model': record._name, 'res_id': record.id, 'body': 'Later',
            'scheduled_date': fields.Datetime.add(fields.Datetime.now(), days=3),
            'author_id': self.env.user.partner_id.id, 'is_note': True})

    def test_bug_b_0142_shared_contact_does_not_take_a_company(self):
        """Regression test for B-0142: a contact shared by all companies stays shared, and the
        preview shows no company line.

        Requirements: FR-DRD-0012, BR-DRD-0004
        """
        shared = self.env['res.partner'].create({'name': 'Scope Shared'})
        owned = self.env['res.partner'].create({'name': 'Scope Shared ', 'company_id': self.manager.company_id.id})
        wizard = self._wizard(shared | owned)
        self.assertNotIn('company_id', wizard.value_line_ids.mapped('field_name'))
        wizard.action_merge()
        self.env.invalidate_all()
        self.assertFalse(shared.company_id)

    def test_bug_b_0142_chatter_fields_are_not_filled(self):
        """Regression test for B-0142: the preview offers no chatter or access-token field.

        Requirements: FR-DRD-0009, FR-DRD-0012
        """
        wizard = self._wizard(self.env['res.partner'].create([{'name': 'Scope Chat'}, {'name': 'Scope Chat '}]))
        names = [field.name for field in wizard.sudo()._preview_fields()]
        self.assertFalse([name for name in names if name.startswith(
            ('message_', 'activity_', 'website_message_', 'access_'))])

    def test_bug_b_0143_scheduled_message_of_a_contact_moves(self):
        """Regression test for B-0143: a message scheduled on the merged-away contact follows the merge.

        Requirements: FR-DRD-0011, FR-DRD-0009
        """
        kept = self.env['res.partner'].create({'name': 'Scope Sched'})
        gone = self.env['res.partner'].create({'name': 'Scope Sched '})
        scheduled = self._schedule_note(gone)
        self._wizard(kept | gone).action_merge()
        self.env.invalidate_all()
        self.assertEqual(scheduled.res_id, kept.id)

    def test_bug_b_0143_scheduled_message_of_a_product_moves(self):
        """Regression test for B-0143: the same for a product template.

        Requirements: FR-DRD-0011, FR-DRD-0009
        """
        kept = self.env['product.template'].create({'name': 'Scope Desk'})
        gone = self.env['product.template'].create({'name': 'Scope Desk '})
        scheduled = self._schedule_note(gone)
        self._wizard(kept | gone).action_merge()
        self.env.invalidate_all()
        self.assertEqual(scheduled.res_id, kept.id)


class TestMergeContactFollowers(TransactionCase):
    """Regression test for B-0146: a unique conflict in one link table drops only the conflicting row.

    Requirements: FR-DRD-0011, FR-DRD-0014.
    """

    def test_bug_b_0146_followers_without_conflict_survive_a_conflicting_one(self):
        """Regression test for B-0146: the merged-away contact's follows on documents the kept
        contact does not follow still move when one follow is held by both.

        Requirements: FR-DRD-0011
        """
        manager = self.env['res.users'].create({
            'name': 'Follow Mgr', 'login': 'follow_mgr_b0146', 'group_ids': [Command.set((
                self.env.ref('base.group_user') | self.env.ref('base.group_partner_manager')
                | self.env.ref('new_duplicate_record_detector.group_duplicate_manager')).ids)]})
        kept, gone = self.env['res.partner'].create([{'name': 'Follow Kept'}, {'name': 'Follow Kept '}])
        docs = self.env['res.partner'].create([{'name': f'Follow Doc {number}'} for number in range(3)])
        docs[0].message_subscribe(partner_ids=(kept | gone).ids)
        docs[1:].message_subscribe(partner_ids=gone.ids)
        self.env['duplicate.merge.wizard'].with_user(manager).with_context(
            active_model='res.partner', active_ids=(kept | gone).ids).create({}).action_merge()
        self.env.invalidate_all()
        self.assertEqual(
            self.env['mail.followers'].search_count([('partner_id', '=', kept.id), ('res_id', 'in', docs.ids)]), 3)
