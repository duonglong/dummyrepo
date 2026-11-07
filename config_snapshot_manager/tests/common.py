from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class ConfigSnapshotCase(TransactionCase):
    """Shared users and helpers for the module's tests."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.admin_user = cls.env['res.users'].create({
            'name': 'Snapshot Admin',
            'login': 'snapshot_admin',
            'group_ids': [(6, 0, [cls.env.ref('base.group_system').id])],
        })
        cls.plain_user = cls.env['res.users'].create({
            'name': 'Snapshot Plain',
            'login': 'snapshot_plain',
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id])],
        })

    def _param(self, key, value):
        return self.env['ir.config_parameter'].sudo().create({'key': key, 'value': value})
