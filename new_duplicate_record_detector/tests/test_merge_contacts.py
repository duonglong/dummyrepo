from odoo import Command
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

        def failing(*_args, **_kwargs):
            raise ValueError('step-broke')

        original = native._update_reference_fields
        native._update_reference_fields = failing
        try:
            with self.assertRaisesRegex(UserError, 'step-broke'):
                wizard.action_merge()
        finally:
            native._update_reference_fields = original
        self.env.invalidate_all()
        self.assertTrue(self.ltd.active)
        self.assertEqual(self.acme.phone, '111')
