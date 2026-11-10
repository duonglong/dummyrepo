from datetime import datetime

from lxml import etree

from odoo.exceptions import AccessError
from odoo.tests import TransactionCase

MODULE = 'scheduled_record_reminder_mvp'


class TestHistoryViewsAcceptance(TransactionCase):
    """Acceptance tests of T-004: the history list, the menus and the rule's counters.

    Requirements: FR-SRR-0015, FR-SRR-0018.

    The history is read through the model and its views (arch read with
    ``get_view``); a view is not driven by a form here: the list has no
    dialog, and clicks on filters and smart buttons are left to the build
    (the tests check the filters' and buttons' presence in the arch).
    """

    def setUp(self):
        super().setUp()
        self.Rule = self.env['record.reminder.rule']
        self.Execution = self.env['record.reminder.execution']
        self.partner_model = self.env['ir.model']._get('res.partner')
        self.todo = self.env.ref('mail.mail_activity_data_todo')
        self.ann = self.env['res.users'].create({
            'name': 'Ann', 'login': 'srr_hv_ann',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])],
        })
        self.target = self.env['res.partner'].create({'name': 'SRR-target'})
        self.r1 = self._rule('R1')
        self.r2 = self._rule('R2')

    def _rule(self, name):
        return self.Rule.create({
            'name': name,
            'model_id': self.partner_model.id,
            'date_field_id': self.env['ir.model.fields']._get('res.partner', 'create_date').id,
            'tz': 'UTC', 'channel': 'activity',
            'activity_type_id': self.todo.id,
            'recipient_mode': 'users', 'user_ids': [(6, 0, [self.ann.id])],
        })

    def _line(self, rule, state, moment, error=False):
        return self.Execution.create({
            'rule_id': rule.id, 'res_model': 'res.partner', 'res_id': self.target.id,
            'record_name': 'SRR-target', 'reminder_key': 'k-%s-%s-%s' % (rule.id, state, moment),
            'channel': 'activity', 'state': state, 'executed_at': moment,
            'error_message': error,
        })

    def _arch(self, model, view_type):
        arch = self.env[model].get_view(view_type=view_type)['arch']
        return etree.fromstring(arch)

    def _user(self, login, group_xmlid):
        return self.env['res.users'].create({
            'name': login, 'login': login,
            'group_ids': [(6, 0, [self.env.ref(group_xmlid).id])],
        })

    # --- FR-SRR-0015 ------------------------------------------------------
    def test_history_list_shows_the_required_columns(self):
        """Requirements: FR-SRR-0015."""
        names = set(self._arch('record.reminder.execution', 'list').xpath('//field/@name'))
        self.assertTrue({
            'rule_id', 'record_name', 'reminder_date', 'channel',
            'partner_ids', 'executed_at', 'state', 'error_message'} <= names, names)

    def test_history_search_filters_on_rule_and_state(self):
        """Requirements: FR-SRR-0015. Scenario SC-SRR-0015-1 (the filters exist)."""
        search = self._arch('record.reminder.execution', 'search')
        self.assertIn('rule_id', search.xpath('//field/@name'))
        domains = ' '.join(search.xpath('//filter/@domain'))
        self.assertIn('failed', domains)
        self.assertIn('sent', domains)

    def test_history_filtered_on_rule_and_failed_is_newest_first(self):
        """Requirements: FR-SRR-0015. Scenario SC-SRR-0015-1."""
        self._line(self.r1, 'sent', datetime(2026, 3, 10, 8, 0))
        old = self._line(self.r1, 'failed', datetime(2026, 3, 10, 9, 0), 'No recipient')
        new = self._line(self.r1, 'failed', datetime(2026, 3, 10, 11, 0), 'Mail error')
        self._line(self.r2, 'failed', datetime(2026, 3, 10, 10, 0), 'Other')
        self._line(self.r2, 'sent', datetime(2026, 3, 10, 12, 0))
        found = self.Execution.search([
            ('rule_id', '=', self.r1.id), ('state', '=', 'failed')])
        self.assertEqual(found, new + old)
        self.assertEqual(found.mapped('error_message'), ['Mail error', 'No recipient'])

    def test_history_opens_the_record_from_its_form(self):
        """Requirements: FR-SRR-0015. The form of a line shows the record, which opens from it."""
        action = self.env['ir.actions.act_window'].sudo().search(
            [('res_model', '=', 'record.reminder.execution')])
        self.assertTrue(action, "the history has a window action")
        self.assertIn('list', action[0].view_mode)
        self.assertIn('form', action[0].view_mode)
        form = self._arch('record.reminder.execution', 'form')
        names = set(form.xpath('//field/@name'))
        self.assertTrue({'rule_id', 'res_id', 'state', 'error_message'} <= names, names)

    def test_rule_counts_sent_and_failed_reminders(self):
        """Requirements: FR-SRR-0015. The rule shows how many it sent and how many failed."""
        self._line(self.r1, 'sent', datetime(2026, 3, 10, 8, 0))
        self._line(self.r1, 'sent', datetime(2026, 3, 10, 9, 0))
        self._line(self.r1, 'failed', datetime(2026, 3, 10, 10, 0), 'x')
        self._line(self.r2, 'sent', datetime(2026, 3, 10, 11, 0))
        self.env.flush_all()
        self.assertEqual((self.r1.sent_count, self.r1.failed_count), (2, 1))
        self.assertEqual((self.r2.sent_count, self.r2.failed_count), (1, 0))

    def test_rule_form_has_history_smart_buttons(self):
        """Requirements: FR-SRR-0015. Buttons for the counts, linking to the rule's own history."""
        form = self._arch('record.reminder.rule', 'form')
        self.assertTrue(form.xpath("//button//field[@name='sent_count']"))
        self.assertTrue(form.xpath("//button//field[@name='failed_count']"))

    # --- FR-SRR-0018 ------------------------------------------------------
    def test_history_has_a_menu_under_reminders(self):
        """Requirements: FR-SRR-0015, FR-SRR-0018. The history is reached from the Reminders menu."""
        root = self.env.ref(f'{MODULE}.menu_reminder_root')
        menus = self.env['ir.ui.menu'].sudo().search([('id', 'child_of', root.id)])
        models_ = {menu.action.res_model for menu in menus
                   if menu.action and menu.action._name == 'ir.actions.act_window'}
        self.assertIn('record.reminder.execution', models_)
        self.assertIn('record.reminder.rule', models_)

    def test_user_who_is_not_a_manager_sees_no_reminder_menu(self):
        """Requirements: FR-SRR-0018. Scenario SC-SRR-0018-1 (menus)."""
        sales = self._user('srr_hv_sales', 'base.group_user')
        root = self.env.ref(f'{MODULE}.menu_reminder_root')
        all_menus = self.env['ir.ui.menu'].sudo().search([('id', 'child_of', root.id)])
        self.assertTrue(len(all_menus) >= 3, "root, rules and history menus expected")
        visible = self.env['ir.ui.menu'].with_user(sales).search([('id', 'in', all_menus.ids)])
        self.assertFalse(visible)
        manager = self._user('srr_hv_manager', f'{MODULE}.group_reminder_manager')
        visible = self.env['ir.ui.menu'].with_user(manager).search([('id', 'in', all_menus.ids)])
        self.assertEqual(visible, all_menus)

    def test_user_who_is_not_a_manager_cannot_use_rules_or_history(self):
        """Requirements: FR-SRR-0018. Scenario SC-SRR-0018-1 (other ways are refused)."""
        sales = self._user('srr_hv_sales2', 'base.group_user')
        self._line(self.r1, 'sent', datetime(2026, 3, 10, 8, 0))
        with self.assertRaises(AccessError):
            self.Rule.with_user(sales).create({
                'name': 'Mass mail',
                'model_id': self.partner_model.id,
                'date_field_id': self.env['ir.model.fields']._get('res.partner', 'create_date').id,
                'tz': 'UTC', 'channel': 'activity', 'activity_type_id': self.todo.id,
                'recipient_mode': 'users', 'user_ids': [(6, 0, [self.ann.id])],
            })
        with self.assertRaises(AccessError):
            self.Rule.with_user(sales).search([])
        with self.assertRaises(AccessError):
            self.r1.with_user(sales).action_run_now()
        with self.assertRaises(AccessError):
            self.Execution.with_user(sales).search([])
