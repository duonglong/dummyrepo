from odoo.tests.common import TransactionCase


class FvfCommon(TransactionCase):
    """Shared fixtures: two companies, two nested test groups and three users.

    Requirements: NFR-REL-0001
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company_be = cls.env['res.company'].create({'name': 'BE Company'})
        cls.company_fr = cls.env['res.company'].create({'name': 'FR Company'})
        cls.group_a = cls.env['res.groups'].create({'name': 'FVF Test Group A'})
        cls.group_b = cls.env['res.groups'].create({
            'name': 'FVF Test Group B',
            'implied_ids': [(4, cls.group_a.id)],
        })
        base_user = cls.env.ref('base.group_user')
        cls.user_anna = cls._create_user('fvf_anna', base_user | cls.group_a)
        cls.user_ben = cls._create_user('fvf_ben', base_user)
        cls.user_admin = cls._create_user(
            'fvf_admin', base_user | cls.env.ref('base.group_system'))

    @classmethod
    def _create_user(cls, login, groups):
        return cls.env['res.users'].with_context(no_reset_password=True).create({
            'name': login,
            'login': login,
            'company_id': cls.company_be.id,
            'company_ids': [(6, 0, (cls.company_be | cls.company_fr).ids)],
            'group_ids': [(6, 0, groups.ids)],
        })
