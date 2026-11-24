"""The hub's work queues for supplier requests: pending bank changes, documents to verify, supplier directory.

Requirements: FR-SPM-0015, FR-SPM-0007, FR-SPM-0019, NFR-SPM-0005
"""
from odoo.tests.common import TransactionCase
from odoo.tools.safe_eval import safe_eval

MODULE = 'supplier_procurement_management_hub'


class TestSupplierQueues(TransactionCase):
    """Requirements: FR-SPM-0015, FR-SPM-0007, FR-SPM-0019, NFR-SPM-0005"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.users = {
            role: cls.env['res.users'].with_context(no_reset_password=True).create({
                'name': f'Queue {role}', 'login': f'spm_queue_{role}', 'email': f'{role}@example.com',
                'group_ids': [(6, 0, [cls.env.ref(f'{MODULE}.group_spm_{role}').id])]})
            for role in ('requester', 'department_manager', 'buyer', 'finance', 'auditor', 'manager', 'admin')}
        cls.supplier = cls.env['res.partner'].create({'name': 'Queue Acme', 'is_company': True, 'supplier_rank': 1})

    def _menus(self, role, *xmlids):
        """The given hub menus the user of ``role`` sees, with the hub root they hang under."""
        visible = self.env['ir.ui.menu'].with_user(self.users[role])._visible_menu_ids()
        root = self.env.ref(f'{MODULE}.menu_spm_root')
        return {xmlid for xmlid in xmlids
                if self.env.ref(f'{MODULE}.{xmlid}').id in visible and root.id in visible}

    def _pending_bank_change(self):
        return self.env['procurement.supplier.change']._spm_hold_bank_change(
            self.supplier, {'account_number': 'BE68539007547034', 'bank_name': 'Bank'})

    def test_bug_b_0267_bank_changes_queue_is_listed_for_finance_and_managers(self):
        """Regression test for B-0267: a Finance reviewer finds the pending bank changes in the hub menu.

        Requirements: FR-SPM-0015
        It cannot reach the rendered menu: it reads the menus the user may open.
        """
        queue = 'menu_spm_pending_bank_changes'
        for role, expected in (('finance', {queue}), ('manager', {queue}), ('admin', {queue}),
                               ('buyer', set()), ('auditor', set()), ('requester', set())):
            self.assertEqual(self._menus(role, queue), expected, role)

    def test_bug_b_0267_bank_changes_queue_holds_only_pending_bank_changes(self):
        """Regression test for B-0267: the queue lists pending bank changes and nothing else.

        Requirements: FR-SPM-0015
        """
        pending = self._pending_bank_change()
        refused = self._pending_bank_change()  # replaces the first one: refused
        self.env['procurement.supplier.change'].create({
            'partner_id': self.supplier.id, 'field_name': 'name', 'state': 'pending'})
        action = self.env.ref(f'{MODULE}.procurement_supplier_change_pending_action')
        listed = self.env['procurement.supplier.change'].with_user(self.users['finance']).search(
            safe_eval(action.domain))
        self.assertEqual(listed, refused)
        self.assertNotIn(pending, listed)

    def test_bug_b_0267_dashboard_counts_pending_bank_changes_for_those_who_verify(self):
        """Regression test for B-0267: Finance and managers see a tile counting the pending bank changes.

        Requirements: FR-SPM-0015, FR-SPM-0098
        """
        self._pending_bank_change()
        self.env.flush_all()
        for role, count in (('finance', 1), ('manager', 1), ('buyer', None)):
            tiles = {tile['key']: tile for tile in self.env['procurement.dashboard'].with_user(
                self.users[role]).get_tiles()}
            tile = tiles.get('pending_bank_changes')
            self.assertEqual(tile and tile['count'], count, role)

    def test_bug_b_0267_documents_to_verify_queue_is_listed_for_buyers_and_managers(self):
        """Regression test for B-0267: portal uploads waiting for verification have a menu entry.

        Requirements: FR-SPM-0007, FR-SPM-0019
        """
        queue = 'menu_spm_documents_to_verify'
        for role, expected in (('buyer', {queue}), ('manager', {queue}), ('admin', {queue}),
                               ('finance', set()), ('requester', set())):
            self.assertEqual(self._menus(role, queue), expected, role)
        document_type = self.env['procurement.document.type'].create({'name': 'Queue certificate'})
        waiting = self.env['procurement.supplier.document'].create({
            'partner_id': self.supplier.id, 'document_type_id': document_type.id,
            'verification_state': 'to_verify'})
        verified = self.env['procurement.supplier.document'].create({
            'partner_id': self.supplier.id, 'document_type_id': document_type.id,
            'verification_state': 'verified'})
        action = self.env.ref(f'{MODULE}.procurement_supplier_document_to_verify_action')
        listed = self.env['procurement.supplier.document'].search(safe_eval(action.domain))
        self.assertIn(waiting, listed)
        self.assertNotIn(verified, listed)

    def test_bug_b_0267_supplier_directory_is_listed_for_every_reading_role(self):
        """Regression test for B-0267: the roles that read suppliers have a supplier list in the hub.

        Requirements: NFR-SPM-0005
        """
        directory = 'menu_spm_supplier_directory'
        for role in ('buyer', 'department_manager', 'manager', 'finance', 'auditor', 'admin'):
            self.assertEqual(self._menus(role, directory), {directory}, role)
        self.assertEqual(self._menus('requester', directory), set())
        action = self.env.ref(f'{MODULE}.procurement_supplier_directory_action')
        other = self.env['res.partner'].create({'name': 'Queue Plain contact'})
        listed = self.env['res.partner'].search(safe_eval(action.domain))
        self.assertIn(self.supplier, listed)
        self.assertNotIn(other, listed)


class TestViewGroupQualification(TransactionCase):
    """Buttons and fields inside a view arch name the hub groups with the module prefix.

    Requirements: FR-SPM-0057, FR-SPM-0072, FR-SPM-0075, FR-SPM-0076, FR-SPM-0077

    Cannot reach: pressing the buttons in the rendered form.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.users = {
            role: cls.env['res.users'].with_context(no_reset_password=True).create({
                'name': f'Arch {role}', 'login': f'spm_arch_{role}', 'email': f'{role}@example.com',
                'group_ids': [(6, 0, [cls.env.ref(f'{MODULE}.group_spm_{role}').id])]})
            for role in ('buyer', 'finance', 'manager', 'admin')}

    def _arch(self, role, model, view_type='form'):
        return self.env[model].with_user(self.users[role]).get_view(view_type=view_type)['arch']

    def test_r3_2_exception_decision_buttons_shown(self):
        for role in ('finance', 'manager', 'admin'):
            arch = self._arch(role, 'procurement.reconciliation.exception')
            for button in ('action_spm_review', 'action_spm_open_accept_wizard',
                           'action_spm_open_reject_wizard', 'action_spm_open_resolve_wizard'):
                with self.subTest(role=role, button=button):
                    self.assertIn(button, arch)

    def test_r3_2_run_matching_shown(self):
        for role in ('finance', 'manager', 'admin'):
            with self.subTest(role=role):
                self.assertIn('action_spm_run_matching', self._arch(role, 'procurement.reconciliation'))

    def test_r3_2_not_a_duplicate_shown_to_finance(self):
        self.assertIn('action_spm_open_not_duplicate_wizard', self._arch('finance', 'procurement.reconciliation'))

    def test_r3_2_shipment_events_shown_on_order(self):
        for role in ('buyer', 'manager', 'admin'):
            with self.subTest(role=role):
                self.assertIn('spm_delivery_event_ids', self._arch(role, 'purchase.order'))

    def test_r3_2_no_unqualified_group_in_any_arch(self):
        """The sweep: no arch node of the module names a group without a module prefix."""
        from lxml import etree
        views = self.env['ir.ui.view'].search([
            ('id', 'in', self.env['ir.model.data'].search([
                ('module', '=', MODULE), ('model', '=', 'ir.ui.view')]).mapped('res_id'))])
        bad = []
        for view in views:
            for node in etree.fromstring(view.arch_db.encode()).iter():
                groups = node.get('groups') if isinstance(node.tag, str) else None
                if groups and any('.' not in g for g in groups.replace('!', '').split(',')):
                    bad.append((view.xml_id, node.tag, groups))
        self.assertEqual(bad, [])
