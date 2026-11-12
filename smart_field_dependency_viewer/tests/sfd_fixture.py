"""Shared fixture of the acceptance tests of M2 (T-003, T-004).

The scenarios name made-up fields (quantity, subtotal, x, y). They are built
as real custom (manual) models and fields, so the running registry holds the
dependencies the viewer reads. Nothing here touches the module's own code.
"""
from odoo.tests.common import TransactionCase, new_test_user

CODE = "for rec in self:\n    rec['%s'] = 0"


class SfdFixture(TransactionCase):
    """Base class: custom models x_sfd_a and x_sfd_b, an administrator Anna, an administrator Ben, a plain user."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        Model, Field = env['ir.model'], env['ir.model.fields']
        model_b = Model.create({'name': 'SFD B', 'model': 'x_sfd_b'})
        model_a = Model.create({'name': 'SFD A', 'model': 'x_sfd_a'})

        def plain(model, name, ttype='integer', label=None, **extra):
            return Field.create({
                'name': name, 'model_id': model.id, 'ttype': ttype,
                'field_description': label or name, 'state': 'manual', **extra})

        def computed(model, name, depends, store=False, label=None):
            return plain(model, name, label=label, compute=CODE % name,
                         depends=depends, store=store)

        for name in ('x_quantity', 'x_price', 'x_c0', 'x_q', 'x_lq', 'x_markup'):
            plain(model_a, name)
        plain(model_a, 'x_reference', 'char')
        plain(model_a, 'x_b_id', 'many2one', relation='x_sfd_b')
        computed(model_a, 'x_subtotal', 'x_quantity,x_price', store=True)
        computed(model_a, 'x_note', '')
        previous = 'x_c0'
        for name in ('x_c1', 'x_c2', 'x_c3', 'x_c4'):
            computed(model_a, name, previous)
            previous = name
        computed(model_a, 'x_s', 'x_q')
        computed(model_a, 'x_d', 'x_q')
        computed(model_a, 'x_t', 'x_s,x_d')
        loop_x = computed(model_a, 'x_lx', 'x_lq')
        computed(model_a, 'x_ly', 'x_lx')
        loop_x.write({'depends': 'x_lq,x_ly'})
        computed(model_a, 'x_markup_dep', 'x_markup', label='<b>&x')
        plain(model_b, 'x_partner_id', 'many2one', relation='res.partner')
        plain(model_b, 'x_partner_country_id', 'many2one', relation='res.country',
              related='x_partner_id.country_id')
        plain(model_b, 'x_line_ids', 'one2many', relation='x_sfd_a', relation_field='x_b_id')
        computed(model_b, 'x_total', 'x_line_ids.x_quantity', store=True)

        cls.anna = new_test_user(env, login='sfd_acc_anna', groups='base.group_system')
        cls.ben = new_test_user(env, login='sfd_acc_ben', groups='base.group_system')
        cls.plain_user = new_test_user(env, login='sfd_acc_plain', groups='base.group_user')

    def viewer(self, user=None):
        return self.env['sfd.dependency.viewer'].with_user(user or self.anna)

    def map(self, model, field, user=None):
        return self.viewer(user).get_dependency_map(model, field)

    def set_limit(self, user, value):
        return user.with_user(user).sfd_set_depth_limit(value)

    @staticmethod
    def nodes_of(payload, field):
        return [n for n in payload['nodes'] if n['field'] == field]

    @staticmethod
    def node_marks(payload, node_id, kind=None):
        return [m for m in payload['marks']
                if m['node'] == node_id and (kind is None or m['type'] == kind)]
