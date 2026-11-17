from odoo import fields, models
from odoo.exceptions import AccessError
from odoo.orm.model_classes import add_to_registry
from odoo.tests import new_test_user, tagged

from .common import DGQTestCase

MODULE = 'data_governance_quality_center_app'


def define_test_result():
    """Build the concrete result model used by these tests, outside the module's own models.

    The definition is taken out of the module's model list right away, so a registry
    loaded later never carries it.
    """
    class DgqTestResult(models.Model):
        _name = 'dgq.test.result'
        _inherit = ['data.quality.result.mixin']
        _description = 'DGQ test result'
        _module = MODULE

        name = fields.Char()

    models.MetaModel._module_to_models__[MODULE].remove(DgqTestResult)
    return DgqTestResult


@tagged('post_install', '-at_install')
class TestResultAccess(DGQTestCase):
    """Requirements: BR-DGQ-0011, FR-DGQ-0071, NFR-DGQ-0006"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        registry = cls.registry
        add_to_registry(registry, define_test_result())
        registry._setup_models__(cls.env.cr)
        registry.init_models(cls.env.cr, ['dgq.test.result'], {'module': MODULE})
        cls.addClassCleanup(registry.models.pop, 'dgq.test.result', None)
        cls.Result = cls.env['dgq.test.result']
        # a test-only model has no access row of the module: the viewer level may read it
        cls.env['ir.access'].create({
            'name': 'dgq test result viewer',
            'model_id': cls.env['ir.model']._get('dgq.test.result').id,
            'group_id': cls.env.ref('%s.group_dgq_viewer' % MODULE).id,
            'operation': 'crud',
        })
        cls.company_a = cls.env.company
        cls.company_b = cls.env['res.company'].create({'name': 'DGQ Company B'})
        cls.viewer = new_test_user(
            cls.env, login='dgq_result_viewer',
            groups='base.group_user,%s.group_dgq_viewer' % MODULE)
        cls.viewer.company_ids = cls.company_a
        cls.viewer.company_id = cls.company_a
        cls.rows = {
            key: cls.Result.create({'name': key, 'res_model': model, 'company_id': company.id})
            for key, model, company in (
                ('a_partner', 'res.partner', cls.company_a),
                ('b_partner', 'res.partner', cls.company_b),
                ('a_cron', 'ir.cron', cls.company_a),
            )
        }
        cls.rows['shared'] = cls.Result.create({'name': 'shared', 'res_model': 'res.partner'})

    def _names(self, user):
        return set(self.Result.with_user(user).search([]).mapped('name'))

    def test_readable_set_does_not_loop_on_result_models(self):
        """BR-DGQ-0011: the readable set is built although a concrete result model exists."""
        readable = self.env['data.quality.result.mixin'].with_user(
            self.viewer)._get_readable_model_names()
        self.assertIn('res.partner', readable)
        self.assertNotIn('dgq.test.result', readable)

    def test_search_returns_only_readable_models_of_allowed_companies(self):
        """BR-DGQ-0011, FR-DGQ-0071 (SC-DGQ-0071-1/2): company A's partner results and shared
        rows are shown; company B and models the user cannot read are not."""
        self.assertEqual(self._names(self.viewer), {'a_partner', 'shared'})

    def test_company_widens_with_allowed_companies(self):
        """FR-DGQ-0071: a user allowed both companies sees both."""
        self.viewer.company_ids = self.company_a | self.company_b
        both = self.Result.with_user(self.viewer).with_context(
            allowed_company_ids=[self.company_a.id, self.company_b.id])
        self.assertEqual(
            set(both.search([]).mapped('name')), {'a_partner', 'b_partner', 'shared'})

    def test_opening_a_hidden_row_is_refused(self):
        """BR-DGQ-0011: reading or writing a row of another company or an unreadable model raises."""
        for key in ('b_partner', 'a_cron'):
            row = self.rows[key].with_user(self.viewer)
            with self.assertRaises(AccessError):
                row.name
            with self.assertRaises(AccessError):
                row.write({'name': 'x'})

    def test_superuser_and_sudo_are_not_filtered(self):
        """NFR-DGQ-0006: scans run in sudo and see every result; a sudo env of a user too."""
        self.assertEqual(len(self.Result.sudo().search([])), 4)
        sudo_as_viewer = self.Result.with_user(self.viewer).sudo()
        self.assertEqual(len(sudo_as_viewer.search([])), 4)

    def test_sudo_does_not_poison_the_readable_cache(self):
        """NFR-DGQ-0006: a sudo call of the user's id does not make the cache hold every model."""
        mixin = self.env['data.quality.result.mixin']
        self.env.transaction.invalidate_ormcache()
        mixin.with_user(self.viewer).sudo()._get_readable_model_names()
        self.assertNotIn(
            'ir.cron', mixin.with_user(self.viewer)._get_readable_model_names())
