from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestGroupAccessAcceptance(TransactionCase):
    """Acceptance tests of T-012 (limit profiling to the Data Profiling group).

    Requirements: FR-DPA-0032. The menu is checked through the root menus the
    user is shown (what the main menu loads); "opening by its link" is the
    read of the record by that user, as the web client does.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.group = cls.env['res.groups'].search([('name', '=', 'Data Profiling')], limit=1)
        user_group = cls.env.ref('base.group_user')
        Users = cls.env['res.users'].with_context(no_reset_password=True)
        cls.paul = Users.create({
            'name': 'Paul Outsider', 'login': 'paul_dpa', 'email': 'paul@example.com',
            'group_ids': [(6, 0, user_group.ids)],
        })
        cls.ben = Users.create({
            'name': 'Ben Member', 'login': 'ben_dpa', 'email': 'ben@example.com',
            'group_ids': [(6, 0, user_group.ids + cls.group.ids)],
        })
        cls.partner_model = cls.env['ir.model']._get('res.partner')

    def setUp(self):
        super().setUp()
        self.profile = self.env['dpa.profile'].create({
            'name': 'Customer quality', 'model_id': self.partner_model.id,
        })
        self.snapshot = self.env['dpa.snapshot'].create({
            'name': 'Customer quality 2026-12-01',
            'profile_id': self.profile.id,
            'model_name': 'res.partner',
            'run_date': '2026-12-01 08:00:00',
        })

    def _root_names(self, user):
        return set(self.env['ir.ui.menu'].with_user(user).get_user_roots().mapped('name'))

    def test_group_exists(self):
        """Requirements: FR-DPA-0032, SC-DPA-0032-1 (the group the rule names exists)."""
        self.assertTrue(self.group, "the Data Profiling group must exist")

    def test_outsider_sees_no_menu(self):
        """Requirements: FR-DPA-0032, SC-DPA-0032-1 (no Data Profiling menu for Paul)."""
        self.assertTrue(self.group)
        self.assertNotIn('Data Profiling', self._root_names(self.paul))

    def test_outsider_refused_snapshot(self):
        """Requirements: FR-DPA-0032, SC-DPA-0032-1 (access error on the snapshot)."""
        snapshot = self.snapshot.with_user(self.paul)
        with self.assertRaises(AccessError):
            snapshot.read(['name'])
        with self.assertRaises(AccessError):
            self.env['dpa.snapshot'].with_user(self.paul).search([])

    def test_outsider_refused_profile_and_comparison(self):
        """Requirements: FR-DPA-0032, SC-DPA-0032-1 (profile and comparison refused too)."""
        with self.assertRaises(AccessError):
            self.profile.with_user(self.paul).read(['name'])
        with self.assertRaises(AccessError):
            self.env['dpa.snapshot.compare'].with_user(self.paul).create({
                'snapshot_a_id': self.snapshot.id, 'snapshot_b_id': self.snapshot.id,
            })

    def test_member_sees_menu(self):
        """Requirements: FR-DPA-0032, SC-DPA-0032-2 (Ben sees Data Profiling)."""
        self.assertIn('Data Profiling', self._root_names(self.ben))

    def test_member_sees_profile_and_snapshots(self):
        """Requirements: FR-DPA-0032, SC-DPA-0032-2 (Ben sees the profile and its snapshots)."""
        profile = self.env['dpa.profile'].with_user(self.ben).search(
            [('name', '=', 'Customer quality')])
        self.assertEqual(profile, self.profile)
        self.assertEqual(profile.snapshot_ids.ids, self.snapshot.ids)
        self.assertEqual(
            self.env['dpa.snapshot'].with_user(self.ben).search(
                [('id', '=', self.snapshot.id)]),
            self.snapshot)
