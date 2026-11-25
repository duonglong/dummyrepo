from odoo.tests import TransactionCase


class DpaCase(TransactionCase):
    """Shared users: an outsider and a member of the Data Profiling group."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.group = cls.env.ref('data_profiling_analysis_app.group_data_profiling')
        internal = cls.env.ref('base.group_user')
        Users = cls.env['res.users'].with_context(no_reset_password=True)
        cls.outsider = Users.create({
            'name': 'Skeleton Outsider', 'login': 'dpa_skel_out', 'email': 'out@example.com',
            'group_ids': [(6, 0, internal.ids)],
        })
        cls.member = Users.create({
            'name': 'Skeleton Member', 'login': 'dpa_skel_in', 'email': 'in@example.com',
            'group_ids': [(6, 0, (internal | cls.group).ids)],
        })
