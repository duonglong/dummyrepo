"""Acceptance tests of T-001 (M1): reminder rule model, refusals, switch-on
moment, access group, menu and form.

Requirements: FR-SRR-0001, FR-SRR-0002, FR-SRR-0016, FR-SRR-0017,
FR-SRR-0018, NFR-SRR-0002.
"""
from datetime import timedelta

from odoo import fields
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import Form, TransactionCase, freeze_time

MODULE = 'scheduled_record_reminder_mvp'


class TestReminderRuleAcceptance(TransactionCase):
    """Requirements: FR-SRR-0001, FR-SRR-0002, FR-SRR-0016, FR-SRR-0017."""

    def setUp(self):
        super().setUp()
        cls = self
        cls.Rule = cls.env['record.reminder.rule']
        cls.partner_model = cls.env['ir.model']._get('res.partner')
        cls.country_model = cls.env['ir.model']._get('res.country')
        cls.activity_type = cls.env.ref('mail.mail_activity_data_todo')
        cls.manager = cls.env['res.users'].create({
            'name': 'Reminder Manager', 'login': 'srr_manager',
            'group_ids': [(6, 0, [
                cls.env.ref('base.group_user').id,
                cls.env.ref(f'{MODULE}.group_reminder_manager').id])],
        })
        cls.user = cls.env['res.users'].create({
            'name': 'Plain User', 'login': 'srr_plain',
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id])],
        })

    def _field(self, model, name):
        return self.env['ir.model.fields']._get(model, name)

    def _vals(self, **kw):
        vals = {
            'name': 'Partner reminder',
            'model_id': self.partner_model.id,
            'date_field_id': self._field('res.partner', 'create_date').id,
            'offset': 3,
            'offset_unit': 'days',
            'offset_direction': 'before',
            'channel': 'activity',
            'activity_type_id': self.activity_type.id,
            'recipient_mode': 'field',
            'recipient_field_id': self._field('res.partner', 'user_id').id,
        }
        vals.update(kw)
        return vals

    def _template(self, model='res.partner'):
        return self.env['mail.template'].create({
            'name': 'SRR tpl', 'model_id': self.env['ir.model']._get(model).id,
        })

    # --- FR-SRR-0001 ------------------------------------------------------
    def test_create_rule_through_form_is_active_with_switch_on_moment(self):
        """Requirements: FR-SRR-0001. Scenario SC-SRR-0001-1.

        Uses the Form helper on the rule form: the fields are those of the
        design (record type, date field, offset, unit, direction, channel,
        activity type, recipient mode and field).
        """
        with freeze_time('2026-03-10 09:00:00'):
            with Form(self.Rule.with_user(self.manager)) as f:
                f.name = 'Contract ends'
                f.model_id = self.partner_model
                f.date_field_id = self._field('res.partner', 'create_date')
                f.offset = 3
                f.offset_unit = 'days'
                f.offset_direction = 'before'
                f.channel = 'activity'
                f.activity_type_id = self.activity_type
                f.recipient_mode = 'field'
                f.recipient_field_id = self._field('res.partner', 'user_id')
            rule = f.record
            self.env.flush_all()
            self.assertTrue(rule.active)
            self.assertEqual(rule.offset, 3)
            self.assertEqual(rule.offset_direction, 'before')
            self.assertEqual(rule.activated_at, fields.Datetime.now())
            self.assertEqual(rule.model_name, 'res.partner')

    def test_defaults(self):
        """Requirements: FR-SRR-0001. Design defaults of a new rule."""
        rule = self.Rule.create(self._vals())
        self.assertTrue(rule.active)
        self.assertEqual(rule.domain or '[]', '[]')
        self.assertTrue(rule.tz)
        rule2 = self.Rule.create({
            'name': 'defaults', 'model_id': self.partner_model.id,
            'date_field_id': self._field('res.partner', 'create_date').id,
            'activity_type_id': self.activity_type.id,
            'recipient_field_id': self._field('res.partner', 'user_id').id,
        })
        self.assertEqual(rule2.offset, 0)
        self.assertEqual(rule2.offset_unit, 'days')
        self.assertEqual(rule2.offset_direction, 'before')
        self.assertEqual(rule2.channel, 'activity')
        self.assertEqual(rule2.recipient_mode, 'field')

    def test_date_field_choice_offers_only_date_fields_of_the_model(self):
        """Requirements: FR-SRR-0001. Scenario SC-SRR-0001-2.

        The date field is offered through a domain of the field; a field that
        is not a date, or is a date of another record type, is refused on save.
        """
        domain = self.Rule.fields_get(['date_field_id'])['date_field_id']['domain']
        self.assertIn('datetime', str(domain))
        self.assertIn('model_id', str(domain))
        name_field = self._field('res.partner', 'name')
        with self.assertRaises(ValidationError):
            self.Rule.create(self._vals(date_field_id=name_field.id))
        other_date = self._field('res.country', 'create_date')
        with self.assertRaises(ValidationError):
            self.Rule.create(self._vals(date_field_id=other_date.id))

    def test_offset_boundary(self):
        """Requirements: FR-SRR-0001/0002. Offset 0 accepted, -1 refused."""
        self.assertEqual(self.Rule.create(self._vals(offset=0)).offset, 0)
        with self.assertRaises(ValidationError):
            self.Rule.create(self._vals(offset=-1))

    def test_hours_after_rule_is_stored(self):
        """Requirements: FR-SRR-0001. Offset in hours and after."""
        rule = self.Rule.create(self._vals(offset_unit='hours', offset_direction='after'))
        self.assertEqual((rule.offset_unit, rule.offset_direction), ('hours', 'after'))

    # --- FR-SRR-0002 ------------------------------------------------------
    def test_email_rule_without_template_refused(self):
        """Requirements: FR-SRR-0002. Scenario SC-SRR-0002-1."""
        with self.assertRaisesRegex(ValidationError, r'(?i)template'):
            self.Rule.create(self._vals(channel='email', activity_type_id=False))

    def test_email_rule_with_template_and_partner_field_accepted(self):
        """Requirements: FR-SRR-0001/0002. Email with a template of the record
        type, recipients from a contact field (allowed for email only)."""
        rule = self.Rule.create(self._vals(
            channel='email', mail_template_id=self._template().id,
            activity_type_id=False,
            recipient_field_id=self._field('res.partner', 'parent_id').id))
        self.assertEqual(rule.channel, 'email')

    def test_email_template_of_other_record_type_refused(self):
        """Requirements: FR-SRR-0002. Template must be of the record type."""
        with self.assertRaises(ValidationError):
            self.Rule.create(self._vals(
                channel='email', activity_type_id=False,
                mail_template_id=self._template('res.country').id))

    def test_invalid_conditions_refused(self):
        """Requirements: FR-SRR-0002. Scenario SC-SRR-0002-2."""
        with self.assertRaisesRegex(ValidationError, r'(?i)condition'):
            self.Rule.create(self._vals(domain="[('no_such_field_xyz', '=', 1)]"))
        rule = self.Rule.create(self._vals(domain="[('name', 'ilike', 'a')]"))
        with self.assertRaisesRegex(ValidationError, r'(?i)condition'):
            rule.write({'domain': "[('no_such_field_xyz', '=', 1)]"})

    def test_users_mode_without_user_refused(self):
        """Requirements: FR-SRR-0002. Scenario SC-SRR-0002-3."""
        with self.assertRaisesRegex(ValidationError, r'(?i)recipient'):
            self.Rule.create(self._vals(
                recipient_mode='users', recipient_field_id=False, user_ids=False))
        rule = self.Rule.create(self._vals(
            recipient_mode='users', recipient_field_id=False,
            user_ids=[(6, 0, self.user.ids)]))
        self.assertEqual(rule.user_ids, self.user)

    def test_field_mode_without_field_refused(self):
        """Requirements: FR-SRR-0002. No field and no user: no recipient."""
        with self.assertRaisesRegex(ValidationError, r'(?i)recipient'):
            self.Rule.create(self._vals(recipient_field_id=False))

    def test_activity_on_record_type_without_activities_refused(self):
        """Requirements: FR-SRR-0002. Countries cannot hold activities."""
        with self.assertRaisesRegex(ValidationError, r'(?i)activit'):
            self.Rule.create(self._vals(
                model_id=self.country_model.id,
                date_field_id=self._field('res.country', 'create_date').id,
                recipient_mode='users', recipient_field_id=False,
                user_ids=[(6, 0, self.user.ids)]))

    def test_recipient_field_must_point_to_users_for_activity(self):
        """Requirements: FR-SRR-0002. A contact field is refused for activity."""
        with self.assertRaises(ValidationError):
            self.Rule.create(self._vals(
                recipient_field_id=self._field('res.partner', 'parent_id').id))

    def test_refused_save_stores_nothing(self):
        """Requirements: FR-SRR-0002. A refused save leaves no rule."""
        count = self.Rule.with_context(active_test=False).search_count([])
        with self.assertRaises(ValidationError):
            self.Rule.create(self._vals(channel='email', activity_type_id=False))
        self.assertEqual(self.Rule.with_context(active_test=False).search_count([]), count)

    # --- FR-SRR-0016 ------------------------------------------------------
    def test_switch_off_and_on_records_new_switch_on_moment(self):
        """Requirements: FR-SRR-0016. Scenario SC-SRR-0016-1 (the switch-on
        moment part; the job's refusal to send a backlog belongs to the
        engine task)."""
        with freeze_time('2026-03-10 09:00:00'):
            rule = self.Rule.create(self._vals())
            first = rule.activated_at
            self.assertEqual(first, fields.Datetime.now())
        with freeze_time('2026-03-12 10:00:00'):
            rule.write({'active': False})
            self.assertEqual(rule.activated_at, first)
        with freeze_time('2026-03-15 11:30:00'):
            rule.write({'active': True})
            self.assertEqual(rule.activated_at, fields.Datetime.now())
            self.assertGreater(rule.activated_at, first)

    def test_rule_created_switched_off_has_no_switch_on_moment(self):
        """Requirements: FR-SRR-0016. Set on create only when active."""
        rule = self.Rule.create(self._vals(active=False))
        self.assertFalse(rule.activated_at)
        with freeze_time('2026-04-01 08:00:00'):
            rule.write({'active': True})
            self.assertEqual(rule.activated_at, fields.Datetime.now())

    def test_switch_on_moment_is_not_copied(self):
        """Requirements: FR-SRR-0001. activated_at is copy=False."""
        rule = self.Rule.create(self._vals())
        self.assertTrue(rule.activated_at)
        with freeze_time(fields.Datetime.now() + timedelta(days=1)):
            copy = rule.copy({'name': 'Copy'})
            self.assertEqual(copy.activated_at, fields.Datetime.now())

    # --- FR-SRR-0017 ------------------------------------------------------
    def test_changing_timing_keeps_the_rule_and_switch_on_moment(self):
        """Requirements: FR-SRR-0017. Offset, date field and conditions can
        change; the rule (and so its history) stays, its switch-on moment
        is untouched. History lines belong to a later task."""
        rule = self.Rule.create(self._vals())
        rid, moment = rule.id, rule.activated_at
        rule.write({
            'offset': 1,
            'domain': "[('name', 'ilike', 'a')]",
            'date_field_id': self._field('res.partner', 'write_date').id,
        })
        self.assertEqual(rule.id, rid)
        self.assertEqual(rule.offset, 1)
        self.assertEqual(rule.activated_at, moment)
        self.assertTrue(rule.active)


class TestReminderAccessAcceptance(TransactionCase):
    """Requirements: FR-SRR-0018, NFR-SRR-0002."""

    def setUp(self):
        super().setUp()
        cls = self
        cls.Rule = cls.env['record.reminder.rule']
        cls.user = cls.env['res.users'].create({
            'name': 'Sales User', 'login': 'srr_sales',
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id])],
        })
        cls.manager = cls.env['res.users'].create({
            'name': 'Reminder Manager', 'login': 'srr_mgr2',
            'group_ids': [(6, 0, [
                cls.env.ref('base.group_user').id,
                cls.env.ref(f'{MODULE}.group_reminder_manager').id])],
        })
        cls.admin = cls.env.ref('base.user_admin')
        cls.vals = {
            'name': 'Access rule',
            'model_id': cls.env['ir.model']._get('res.partner').id,
            'date_field_id': cls.env['ir.model.fields']._get('res.partner', 'create_date').id,
            'channel': 'activity',
            'activity_type_id': cls.env.ref('mail.mail_activity_data_todo').id,
            'recipient_mode': 'field',
            'recipient_field_id': cls.env['ir.model.fields']._get('res.partner', 'user_id').id,
        }

    def _rule_menus(self):
        menus = self.env['ir.ui.menu'].sudo().with_context(active_test=False).search([])
        return menus.filtered(
            lambda m: m.action and m.action._name == 'ir.actions.act_window'
            and m.action.res_model == 'record.reminder.rule')

    def test_plain_user_sees_no_menu_and_cannot_create(self):
        """Requirements: FR-SRR-0018. Scenario SC-SRR-0018-1."""
        menus = self._rule_menus()
        self.assertTrue(menus, 'a reminder rule menu must exist')
        visible = self.env['ir.ui.menu'].with_user(self.user)._visible_menu_ids()
        self.assertFalse(set(menus.ids) & set(visible))
        with self.assertRaises(AccessError):
            self.Rule.with_user(self.user).create(self.vals)
        with self.assertRaises(AccessError):
            self.Rule.with_user(self.user).search([])

    def test_plain_user_cannot_change_or_delete_a_rule(self):
        """Requirements: FR-SRR-0018. Change and delete are refused too."""
        rule = self.Rule.with_user(self.manager).create(self.vals)
        with self.assertRaises(AccessError):
            rule.with_user(self.user).write({'name': 'x'})
        with self.assertRaises(AccessError):
            rule.with_user(self.user).unlink()

    def test_manager_sees_menu_and_manages_rules(self):
        """Requirements: FR-SRR-0018. A reminder manager works with rules."""
        menus = self._rule_menus()
        visible = self.env['ir.ui.menu'].with_user(self.manager)._visible_menu_ids()
        self.assertTrue(set(menus.ids) & set(visible))
        rule = self.Rule.with_user(self.manager).create(self.vals)
        rule.write({'name': 'renamed'})
        self.assertEqual(rule.name, 'renamed')
        rule.unlink()
        self.assertFalse(rule.exists())

    def test_administrator_is_manager_without_extra_setup(self):
        """Requirements: FR-SRR-0018. Scenario SC-SRR-0018-2."""
        menus = self._rule_menus()
        visible = self.env['ir.ui.menu'].with_user(self.admin)._visible_menu_ids()
        self.assertTrue(set(menus.ids) & set(visible))
        rule = self.Rule.with_user(self.admin).create(self.vals)
        self.assertTrue(rule.active)
        group = self.env.ref(f'{MODULE}.group_reminder_manager')
        self.assertIn(group, self.admin.group_ids)

    def test_module_depends_only_on_community_apps(self):
        """Requirements: NFR-SRR-0002. No Enterprise module is required."""
        module = self.env['ir.module.module'].search([('name', '=', MODULE)])
        deps = set(module.dependencies_id.mapped('name'))
        self.assertTrue(deps, 'module must declare its dependencies')
        self.assertIn('mail', deps)
        for dep in deps:
            m = self.env['ir.module.module'].search([('name', '=', dep)])
            self.assertNotEqual(m.license, 'OEEL-1', dep)
