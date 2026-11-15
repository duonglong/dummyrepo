import io

import openpyxl

from odoo.exceptions import UserError, ValidationError
from odoo.tests import Form

from odoo.addons.data_governance_quality_center_app.services import profile_compare

from .acceptance_m3_helpers import M3Case


class TestProfileCompare(M3Case):
    """Own tests of T-022: comparing two profiles and exporting a profile.

    The comparison dialog is opened with ``Form`` in the action's context; the Compare and Export
    buttons are left to the screen review.

    Requirements: FR-DGQ-0056, FR-DGQ-0057
    """

    def _contacts(self, empty, filled, prefix):
        self.env['res.partner'].create(
            [{'name': '%s e%s' % (prefix, n), 'company_id': self.cx.id} for n in range(empty)]
            + [{'name': '%s f%s' % (prefix, n), 'email': '%s%s@example.com' % (prefix, n),
                'company_id': self.cx.id} for n in range(filled)])

    def _profile(self, fields_=('email',), **vals):
        return self.run_profile(self.make_profile('res.partner', list(fields_), **vals))

    def _wizard(self, profile, earlier):
        action = profile.action_compare()
        with Form(self.env[action['res_model']].with_context(**action['context'])) as form:
            if form.compare_profile_id != earlier:
                form.compare_profile_id = earlier
        return form.save()

    def test_empty_share_change_in_points(self):
        """SC-DGQ-0056-1: e-mail empty from 25 to 8 percent shows a drop of 17 points."""
        self.isolate('res.partner')
        self._contacts(25, 75, 'a')
        old = self._profile()
        self.env['res.partner'].search([('name', '=like', 'a e%')], limit=17).write({'email': 'y@example.com'})
        new = self._profile()
        wizard = self._wizard(new, old)
        line = wizard.line_ids.filtered(lambda l: l.field_name == 'email')
        self.assertEqual(line.status, 'changed')
        self.assertAlmostEqual(line.empty_share_old, 25.0)
        self.assertAlmostEqual(line.empty_share_new, 8.0)
        self.assertAlmostEqual(line.empty_share_change, -17.0)
        self.assertEqual(line.distinct_change, 1)

    def test_fields_in_one_profile_only_are_marked(self):
        """FR-DGQ-0056: a field added or dropped is shown as such, not as a change."""
        self.isolate('res.partner')
        self._contacts(1, 1, 'b')
        old = self._profile(('email', 'phone'))
        new = self._profile(('email', 'city'))
        status = {row['field_name']: row['status'] for row in profile_compare.compare_profiles(self.env, new, old)}
        self.assertEqual((status['phone'], status['city'], status['email']), ('dropped', 'added', 'same'))

    def test_shapes_and_number_statistics_changes(self):
        """FR-DGQ-0056: new value shapes and changed number statistics are listed."""
        self.isolate('res.partner')
        self.env['res.partner'].create({'name': 'S', 'zip': '1000', 'company_id': self.cx.id})
        old = self._profile(('zip', 'partner_latitude'))
        self.env['res.partner'].search([('name', '=', 'S')]).write({'zip': 'AB-12', 'partner_latitude': 5.0})
        new = self._profile(('zip', 'partner_latitude'))
        rows = {r['field_name']: r for r in profile_compare.compare_profiles(self.env, new, old)}
        self.assertIn('New: AA-99', rows['zip']['shapes_change'])
        self.assertIn('Gone: 9999', rows['zip']['shapes_change'])
        self.assertIn('Maximum 0.0 -> 5.0', rows['partner_latitude']['numbers_change'])

    def test_other_model_or_company_is_refused(self):
        """FR-DGQ-0056: profiles of another model or company cannot be compared."""
        Profile = self.env['data.quality.profile'].sudo()  # sudo: profiles of any company, set up without a run
        one = Profile.create({'model_id': self.model('res.partner').id, 'company_id': self.cx.id})
        other_company = Profile.create({'model_id': self.model('res.partner').id, 'company_id': self.cy.id})
        product = Profile.create({'model_id': self.model('product.template').id, 'company_id': self.cx.id})
        for other in (other_company, product):
            with self.assertRaises(ValidationError):
                self.env['data.quality.profile.compare.wizard'].sudo().create(  # sudo: set up for the refusal
                    {'profile_id': one.id, 'compare_profile_id': other.id})

    def test_xlsx_has_a_sheet_per_statistic_naming_the_source(self):
        """SC-DGQ-0057-1: the XLSX names model, company, date, user and the per-field figures."""
        self.isolate('res.partner')
        self._contacts(1, 3, 'c')
        profile = self._profile(('email', 'country_id'))
        book = openpyxl.load_workbook(io.BytesIO(profile_compare.profile_xlsx(profile)))
        self.assertEqual(
            book.sheetnames,
            ['Completeness', 'Frequencies', 'Formats', 'Numbers', 'Links', 'Unused Choices'])
        for sheet in book:
            text = [cell for row in sheet.iter_rows(max_row=5, values_only=True) for cell in row]
            for fact in ('Contact', self.cx.name, self.operator.name, profile.create_date.strftime('%Y-%m-%d')):
                self.assertIn(fact, text)
        rows = list(book['Completeness'].iter_rows(min_row=7, values_only=True))
        email = next(row for row in rows if row[0] == 'Email')
        self.assertEqual((email[2], email[4]), (1, 3))

    def test_pdf_report_shows_the_same_figures(self):
        """SC-DGQ-0057-1: the PDF report names the source and the per-field figures."""
        self.isolate('res.partner')
        self._contacts(1, 3, 'd')
        profile = self._profile()
        html = self.env['ir.actions.report']._render_qweb_html(
            'data_governance_quality_center_app.report_profile', profile.ids)[0].decode()
        for fact in ('Contact', self.cx.name, self.operator.name, 'Completeness', 'Email'):
            self.assertIn(fact, html)

    def test_export_leaves_out_a_field_marked_sensitive_after_the_profile(self):
        """FR-DGQ-0057: exports respect the field exclusions that apply now."""
        self.isolate('res.partner')
        self._contacts(1, 1, 'e')
        profile = self._profile(('email', 'city'))
        self.env['data.quality.field.config'].sudo().create(  # sudo: a mark set by an administrator
            {'field_id': self.fld('res.partner', 'city').id, 'status': 'excluded'})
        names = {row[0] for _t, _h, rows in profile_compare.profile_sections(profile) for row in rows}
        self.assertNotIn('City', names)

    def test_only_a_finished_profile_is_exported(self):
        """FR-DGQ-0057: a draft profile has nothing to export."""
        profile = self.make_profile('res.partner', ['email'])
        with self.assertRaises(UserError):
            profile.action_export_xlsx()
        done = self._profile()
        self.assertIn('/data_quality/xlsx/profile/%s' % done.id, done.action_export_xlsx()['url'])
        self.assertEqual(done.action_export_pdf()['report_name'],
                         'data_governance_quality_center_app.report_profile')
