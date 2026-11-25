from odoo.exceptions import AccessError
from odoo.tests import tagged

from .common import DpaCase


@tagged('post_install', '-at_install')
class TestSkeleton(DpaCase):
    """Requirements: FR-DPA-0032, NFR-SEC-0001 (group, menu root, model list read grant)."""

    def test_group_is_not_implied_by_administrators(self):
        """Requirements: FR-DPA-0032 - only people put in the group use the app."""
        admin_group = self.env.ref('base.group_system')
        self.assertNotIn(self.group, admin_group.all_implied_ids)
        self.assertNotIn(self.group, self.env.ref('base.group_user').all_implied_ids)

    def test_menu_root_limited_to_group(self):
        """Requirements: FR-DPA-0032 - the root menu carries the group."""
        menu = self.env.ref('data_profiling_analysis_app.menu_data_profiling_root')
        self.assertEqual(menu.group_ids, self.group)
        self.assertFalse(menu.parent_id)

    def test_member_reads_model_list(self):
        """Requirements: NFR-SEC-0001 - a member can read models and fields."""
        for model in ('ir.model', 'ir.model.fields', 'ir.model.fields.selection'):
            self.env[model].with_user(self.member).check_access('read')

    def test_outsider_cannot_read_model_list(self):
        """Requirements: FR-DPA-0032 - the grant is for the group only."""
        for model in ('ir.model', 'ir.model.fields', 'ir.model.fields.selection'):
            with self.assertRaises(AccessError):
                self.env[model].with_user(self.outsider).check_access('read')
