from odoo.tests import TransactionCase, tagged

MODULE = 'data_governance_quality_center_app'


@tagged('post_install', '-at_install')
class TestUninstall(TransactionCase):
    """Requirements: NFR-DGQ-0009"""

    def test_every_scheduled_action_of_the_app_is_owned_by_the_module(self):
        """Requirements: NFR-DGQ-0009 - uninstalling removes each scheduled action of the app."""
        crons = self.env['ir.cron'].sudo().with_context(active_test=False).search(
            [('model_id.model', '=like', 'data.quality.%')])
        self.assertTrue(crons)
        owned = self.env['ir.model.data'].sudo().search([
            ('model', '=', 'ir.cron'), ('module', '=', MODULE), ('res_id', 'in', crons.ids)]).mapped('res_id')
        self.assertEqual(set(crons.ids), set(owned))

    def test_no_field_of_the_app_is_added_to_a_business_model(self):
        """Requirements: NFR-DGQ-0009 - the app adds no column to partners or products."""
        added = self.env['ir.model.data'].sudo().search([
            ('model', '=', 'ir.model.fields'), ('module', '=', MODULE)])
        fields = self.env['ir.model.fields'].sudo().browse(added.mapped('res_id')).exists()
        foreign = fields.filtered(
            lambda f: f.store and f.model != 'res.config.settings'
            and not f.model.startswith(('data.quality.', 'ir.model')))
        self.assertFalse(foreign.mapped('display_name'))

    def test_every_view_of_the_app_is_owned_by_the_module(self):
        """Requirements: NFR-DGQ-0009 - no view is left behind."""
        views = self.env['ir.ui.view'].sudo().search([('model', '=like', 'data.quality.%')])
        owned = self.env['ir.model.data'].sudo().search([
            ('model', '=', 'ir.ui.view'), ('module', '=', MODULE), ('res_id', 'in', views.ids)]).mapped('res_id')
        self.assertEqual(set(views.ids), set(owned))
