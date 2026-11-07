from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class AcceptanceBase(TransactionCase):
    """Users for the acceptance tests; independent of the module's own test base."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.admin_user = cls.env['res.users'].create({
            'name': 'TCM Admin',
            'login': 'tcm_admin_acc',
            'group_ids': [(6, 0, [cls.env.ref('base.group_system').id])],
        })
        cls.plain_user = cls.env['res.users'].create({
            'name': 'TCM Plain',
            'login': 'tcm_plain_acc',
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id])],
        })

    def _param(self, key, value):
        return self.env['ir.config_parameter'].sudo().create({'key': key, 'value': value})
