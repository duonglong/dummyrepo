from odoo import Command
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import Form
from odoo.tests.common import TransactionCase
from odoo.tools import mute_logger

NOTICE = ("Hiding a field only removes it from this form. "
          "Its data can still be read in lists, reports and exports.")


class TestLayoutModelsAcceptance(TransactionCase):
    """Acceptance tests of T-003: layout editor models, refusals, access, menu, notice.

    Requirements: BR-FVF-0004, FR-FVF-0001, FR-FVF-0016, FR-FVF-0017,
    FR-FVF-0018, NFR-SEC-0001.
    The editor form is driven with Form where the design names its fields
    (model, form view, company); the line dialog and its group choice are
    tested through the line records, the drawing of the dialog is left to
    the build.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner_model = cls.env['ir.model']._get('res.partner')
        cls.partner_view = cls.env.ref('base.view_partner_form')
        cls.company_be = cls.env['res.company'].create({'name': 'BE Company'})
        cls.group = cls.env.ref('base.group_user')
        cls.admin = cls.env['res.users'].create({
            'name': 'Fvf Admin', 'login': 'fvf_admin_acc',
            'group_ids': [Command.set([cls.env.ref('base.group_system').id, cls.env.ref('base.group_user').id])],
        })
        cls.ben = cls.env['res.users'].create({
            'name': 'Fvf Ben', 'login': 'fvf_ben_acc',
            'group_ids': [Command.set([cls.env.ref('base.group_user').id])],
        })

    def _layout(self, company=None):
        return self.env['fvf.layout'].create({
            'model_id': self.partner_model.id,
            'view_id': self.partner_view.id,
            'company_id': company.id if company else False,
        })

    def _line(self, layout, field_name):
        line = layout.line_ids.filtered(lambda l: l.field_name == field_name)
        self.assertTrue(line, "no line for %s" % field_name)
        return line[:1]

    # -- FR-FVF-0001 ------------------------------------------------------------

    def test_start_layout_for_company_with_form(self):
        """SC-FVF-0001-1 (FR-FVF-0001): choosing model, form and company gives a draft listing the form's fields."""
        with Form(self.env['fvf.layout'].with_user(self.admin)) as form:
            form.model_id = self.partner_model
            form.view_id = self.partner_view
            form.company_id = self.company_be
        layout = form.save()
        self.env.flush_all()
        self.assertEqual(layout.state, 'draft')
        self.assertEqual(layout.company_id, self.company_be)
        names = set(layout.line_ids.mapped('field_name'))
        self.assertTrue({'name', 'phone', 'email'} <= names)

    def test_start_shared_default_layout(self):
        """SC-FVF-0001-2 (FR-FVF-0001): an empty company gives a draft shared default layout."""
        with Form(self.env['fvf.layout'].with_user(self.admin)) as form:
            form.model_id = self.partner_model
            form.view_id = self.partner_view
        layout = form.save()
        self.assertEqual(layout.state, 'draft')
        self.assertFalse(layout.company_id)
        self.assertTrue(layout.line_ids)

    def test_one_line_per_field(self):
        """FR-FVF-0001: the layout keeps one line per distinct field of the form."""
        layout = self._layout()
        names = layout.line_ids.mapped('field_name')
        self.assertEqual(len(names), len(set(names)))

    def test_view_must_belong_to_the_model(self):
        """FR-FVF-0001: a form view of another model is refused."""
        other = self.env.ref('base.view_users_form')
        with self.assertRaisesRegex(ValidationError, 'Choose a main form view of the chosen model'):
            with self.cr.savepoint():
                self.env['fvf.layout'].create({
                    'model_id': self.partner_model.id, 'view_id': other.id})

    def test_new_section_starts_open(self):
        """SC-FVF-0004-2 (FR-FVF-0004): a section added without a start state starts open."""
        layout = self._layout()
        section = self.env['fvf.layout.section'].create({'layout_id': layout.id, 'name': 'Notes'})
        self.assertEqual(section.start_state, 'open')

    # -- FR-FVF-0016, BR-FVF-0004 ----------------------------------------------------

    def test_required_field_cannot_be_hidden(self):
        """SC-FVF-0016-1 (FR-FVF-0016, BR-FVF-0004): hiding a required field is refused and nothing changes."""
        line = self._line(self._layout(), 'name')
        self.assertTrue(line.is_required)
        with self.assertRaisesRegex(ValidationError, 'A required field cannot be hidden'):
            with self.cr.savepoint():
                line.write({'hidden_group_ids': [Command.set([self.group.id])]})
        line.invalidate_recordset()
        self.assertFalse(line.hidden_group_ids)

    def test_required_field_cannot_get_a_condition(self):
        """FR-FVF-0016, BR-FVF-0004: a visibility condition on a required field is refused too."""
        line = self._line(self._layout(), 'name')
        with self.assertRaisesRegex(ValidationError, 'A required field cannot be hidden'):
            with self.cr.savepoint():
                line.write({'condition_ids': [Command.create({
                    'field_name': 'is_company', 'operator': 'set'})]})
        line.invalidate_recordset()
        self.assertFalse(line.condition_ids)

    def test_required_field_can_be_moved(self):
        """SC-FVF-0016-2 (FR-FVF-0016): moving a required field into a section is accepted."""
        layout = self._layout()
        section = self.env['fvf.layout.section'].create({'layout_id': layout.id, 'name': 'Identity'})
        line = self._line(layout, 'name')
        line.write({'section_id': section.id})
        self.assertEqual(line.section_id, section)

    def test_optional_field_can_be_hidden(self):
        """FR-FVF-0002: a field that is not required can be hidden from a group."""
        line = self._line(self._layout(), 'phone')
        self.assertFalse(line.is_required)
        line.write({'hidden_group_ids': [Command.set([self.group.id])]})
        self.assertEqual(line.hidden_group_ids, self.group)

    def test_section_must_belong_to_same_layout(self):
        """FR-FVF-0006: a line points only at a section of its own layout."""
        layout, other = self._layout(), self._layout(self.company_be)
        section = self.env['fvf.layout.section'].create({'layout_id': other.id, 'name': 'Elsewhere'})
        line = self._line(layout, 'phone')
        with self.assertRaises(ValidationError):
            with self.cr.savepoint():
                line.write({'section_id': section.id})

    # -- FR-FVF-0017 ---------------------------------------------------------------

    def test_condition_on_own_field_refused(self):
        """SC-FVF-0017-1 (FR-FVF-0017): a condition using the field itself is refused, the line unchanged."""
        line = self._line(self._layout(), 'country_id')
        with self.assertRaisesRegex(
                ValidationError, 'A field cannot be shown or hidden based on its own value'):
            with self.cr.savepoint():
                line.write({'condition_ids': [Command.create({
                    'field_name': 'country_id', 'operator': 'is', 'value': '1'})]})
        line.invalidate_recordset()
        self.assertFalse(line.condition_ids)

    def test_condition_on_other_field_accepted(self):
        """FR-FVF-0005: a condition on another field of the record is saved."""
        line = self._line(self._layout(), 'vat')
        line.write({'condition_ids': [Command.create({
            'field_name': 'country_id', 'operator': 'is', 'value': '1'})]})
        self.assertEqual(len(line.condition_ids), 1)

    def test_comparison_not_allowed_for_kind_of_field(self):
        """FR-FVF-0005 (decision table): greater than on a text field is refused."""
        line = self._line(self._layout(), 'vat')
        with self.assertRaisesRegex(
                ValidationError, 'This comparison cannot be used with this kind of field'):
            with self.cr.savepoint():
                line.write({'condition_ids': [Command.create({
                    'field_name': 'name', 'operator': 'gt', 'value': 'a'})]})

    def test_comparison_needs_a_value(self):
        """FR-FVF-0005: a comparison that needs a value is refused without one."""
        line = self._line(self._layout(), 'vat')
        with self.assertRaisesRegex(ValidationError, 'Enter a value for this comparison'):
            with self.cr.savepoint():
                line.write({'condition_ids': [Command.create({
                    'field_name': 'name', 'operator': 'is'})]})

    def test_is_set_needs_no_value(self):
        """FR-FVF-0005: is set / is not set need no value."""
        line = self._line(self._layout(), 'vat')
        line.write({'condition_ids': [Command.create({
            'field_name': 'is_company', 'operator': 'set'})]})
        self.assertEqual(len(line.condition_ids), 1)

    # -- FR-FVF-0018 ----------------------------------------------------------------

    def test_notice_in_editor_views(self):
        """SC-FVF-0018-1 (FR-FVF-0018): the editor's views carry the not-security notice beside group hiding."""
        arch = ''
        for model in ('fvf.layout', 'fvf.layout.line'):
            arch += self.env[model].get_view(view_type='form')['arch']
        self.assertIn(NOTICE, arch)

    # -- NFR-SEC-0001 -----------------------------------------------------------------

    def test_settings_admin_can_create_and_read(self):
        """NFR-SEC-0001: a Settings administrator creates and reads layouts."""
        layout = self.env['fvf.layout'].with_user(self.admin).create({
            'model_id': self.partner_model.id, 'view_id': self.partner_view.id})
        self.assertTrue(layout.with_user(self.admin).name)

    @mute_logger('odoo.addons.base.models.ir_model')
    def test_other_user_cannot_create(self):
        """NFR-SEC-0001: a user outside the Settings administration group cannot create a layout."""
        with self.assertRaises(AccessError):
            self.env['fvf.layout'].with_user(self.ben).create({
                'model_id': self.partner_model.id, 'view_id': self.partner_view.id})

    @mute_logger('odoo.addons.base.models.ir_model')
    def test_other_user_cannot_read(self):
        """NFR-SEC-0001: a user outside the Settings administration group cannot read layouts."""
        layout = self._layout()
        with self.assertRaises(AccessError):
            layout.with_user(self.ben).read(['name'])
        with self.assertRaises(AccessError):
            self.env['fvf.layout.line'].with_user(self.ben).search([])

    def test_menu_under_user_interface_for_admins_only(self):
        """NFR-SEC-0001: the Form layouts menu sits under Technical > User Interface, for Settings admins only."""
        menu = self.env['ir.ui.menu'].search([
            ('name', '=', 'Form layouts'), ('parent_id', '=', self.env.ref('base.next_id_2').id)])
        self.assertEqual(len(menu), 1)
        self.assertEqual(menu.group_ids, self.env.ref('base.group_system'))
