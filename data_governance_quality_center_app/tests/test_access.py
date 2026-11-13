from odoo.exceptions import AccessError
from odoo.tests import new_test_user

from .common import DGQTestCase


class TestAccess(DGQTestCase):
    """Tests for FR-DGQ-0070, BR-DGQ-0011 and NFR-DGQ-0006 (access levels and result access)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.users = {
            level: new_test_user(
                cls.env, login='dgq_%s' % level,
                groups='base.group_user,data_governance_quality_center_app.group_dgq_%s' % level)
            for level in ('viewer', 'operator', 'manager', 'admin')
        }

    def test_each_level_includes_the_one_before(self):
        """FR-DGQ-0070: Viewer < Operator < Manager < Administrator."""
        chain = ['viewer', 'operator', 'manager', 'admin']
        for index, level in enumerate(chain):
            for lower in chain[:index + 1]:
                group = 'data_governance_quality_center_app.group_dgq_%s' % lower
                self.assertTrue(self.users[level].has_group(group), (level, lower))
        self.assertFalse(self.users['viewer'].has_group(
            'data_governance_quality_center_app.group_dgq_operator'))

    def test_odoo_administrator_is_data_quality_administrator(self):
        """FR-DGQ-0070: the Odoo administrator group implies the top level."""
        self.assertTrue(self.env.ref('base.user_admin').has_group(
            'data_governance_quality_center_app.group_dgq_admin'))

    def test_viewer_reads_but_does_not_change_marks(self):
        """FR-DGQ-0070: the server refuses a write of a viewer, even called directly."""
        viewer = self.users['viewer']
        for name in ('data.quality.model.config', 'data.quality.field.config'):
            self.assertTrue(self.env[name].with_user(viewer).has_access('read'))
            for operation in ('create', 'write', 'unlink'):
                self.assertFalse(self.env[name].with_user(viewer).has_access(operation))
        config = self.env['data.quality.model.config'].search([], limit=1)
        with self.assertRaises(AccessError):
            config.with_user(self.users['manager']).write({'status': 'excluded'})

    def test_only_administrator_changes_marks(self):
        """FR-DGQ-0070: model and field marks are administrator settings."""
        admin = self.users['admin']
        for name in ('data.quality.model.config', 'data.quality.field.config'):
            for operation in ('read', 'create', 'write', 'unlink'):
                self.assertTrue(self.env[name].with_user(admin).has_access(operation))

    def test_user_without_level_sees_nothing(self):
        """FR-DGQ-0070: an internal user without a level cannot read the marks."""
        plain = new_test_user(self.env, login='dgq_plain', groups='base.group_user')
        self.assertFalse(self.env['data.quality.model.config'].with_user(plain).has_access('read'))

    def test_readable_models_follow_the_users_rights(self):
        """BR-DGQ-0011: results are limited to models the user may read."""
        mixin = self.env['data.quality.result.mixin']
        plain = new_test_user(self.env, login='dgq_plain2', groups='base.group_user')
        readable = mixin.with_user(plain)._get_readable_model_names()
        self.assertIn('res.partner', readable)
        self.assertNotIn('ir.cron', readable)
        self.assertIn('ir.cron', mixin.with_user(self.env.ref('base.user_admin'))._get_readable_model_names())
