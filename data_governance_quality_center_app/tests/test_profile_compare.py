import io

import openpyxl

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import Form, HttpCase, new_test_user, tagged

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

    def test_later_or_unfinished_profile_cannot_be_the_earlier_one(self):
        """FR-DGQ-0056: only a finished profile made before the profile is its comparison base."""
        self.isolate('res.partner')
        self._contacts(1, 1, 'f')
        old = self._profile()
        new = self._profile()
        draft = self.make_profile('res.partner', ['email'])
        Wizard = self.env['data.quality.profile.compare.wizard'].sudo()  # sudo: set up for the refusals
        Wizard.create({'profile_id': new.id, 'compare_profile_id': old.id})
        for profile_id, compare_id in ((old.id, new.id), (new.id, draft.id), (draft.id, old.id)):
            with self.assertRaises(ValidationError):
                Wizard.create({'profile_id': profile_id, 'compare_profile_id': compare_id})
        for profile, other in ((old, new), (new, new)):
            with self.assertRaises(ValidationError):
                profile.sudo().compare_profile_id = other  # sudo: the constraint is the guard
        new.sudo().compare_profile_id = old  # sudo: set up as a manager would

    def test_dialog_shows_no_figures_against_an_unfinished_earlier_profile(self):
        """FR-DGQ-0056: opened with Form (never saved), the dialog gives no rows for a draft or later base.

        The Compare button is left to the screen review.
        """
        self.isolate('res.partner')
        self._contacts(1, 1, 'h')
        old = self._profile()
        new = self._profile()
        draft = self.make_profile('res.partner', ['email'])
        new.sudo().compare_profile_id = old  # sudo: set up as a manager would
        action = new.action_compare()
        with Form(self.env[action['res_model']].with_context(**action['context'])) as form:
            self.assertEqual(form.compare_profile_id, old)
            self.assertTrue(len(form.line_ids))
        self.assertEqual(profile_compare.compare_profiles(self.env, new, draft), [])
        self.assertEqual(profile_compare.compare_profiles(self.env, old, new), [])
        self.assertEqual(profile_compare.compare_profiles(self.env, draft, old), [])
        early_draft = self.make_profile('res.partner', ['email'])
        later = self._profile()
        later.sudo().compare_profile_id = early_draft  # the constraint accepts it; the dialog does not
        action = later.action_compare()
        form = Form(self.env[action['res_model']].with_context(**action['context']))  # never saved, as on screen
        self.assertFalse(form.compare_profile_id)
        self.assertFalse(len(form.line_ids))

    def test_comparison_reads_the_lines_as_the_user(self):
        """FR-DGQ-0056: a viewer of another company gets no figures of a profile of company X."""
        self.isolate('res.partner')
        self._contacts(1, 1, 'g')
        old = self._profile()
        new = self._profile()
        outsider = new_test_user(self.env, login='dgq_cmp_outsider', groups='base.group_user,%s.group_dgq_viewer'
                                 % 'data_governance_quality_center_app')
        outsider.write({'company_ids': [(6, 0, self.cy.ids)], 'company_id': self.cy.id})
        with self.assertRaises(AccessError):
            profile_compare.compare_profiles(self.env(user=outsider), new.with_user(outsider), old.with_user(outsider))
        with self.assertRaises(AccessError):
            profile_compare.profile_sections(new.with_user(outsider))

    def test_dialog_computes_the_figures_as_the_user(self):
        """FR-DGQ-0056, FR-DGQ-0071: the dialog's live update gives an outsider no figures of company X.

        The dialog is filled as the screen's live update does (a new, unsaved record), with the ids of
        two profiles of another company; reading its lines is refused instead of computed as superuser.
        """
        self.isolate('res.partner')
        self._contacts(1, 1, 'i')
        old = self._profile()
        new = self._profile()
        outsider = new_test_user(self.env, login='dgq_cmp_dialog_outsider',
                                 groups='base.group_user,data_governance_quality_center_app.group_dgq_viewer')
        outsider.write({'company_ids': [(6, 0, self.cy.ids)], 'company_id': self.cy.id})
        # assertRaises(AccessError) empties the cache, as a new request does: the unsaved dialog is
        # filled inside it, or its own values would be gone too
        with self.assertRaises(AccessError):
            self.env['data.quality.profile.compare.wizard'].with_user(outsider).new(
                {'profile_id': new.id, 'compare_profile_id': old.id}).line_ids.mapped('empty_share_new')

    def test_pdf_report_of_an_unfinished_profile_is_refused(self):
        """FR-DGQ-0057: the report data of a draft profile is refused, as the button is."""
        draft = self.make_profile('res.partner', ['email'])
        with self.assertRaises(UserError):
            draft.get_export_sections()


@tagged('post_install', '-at_install')
class TestProfileXlsxRoute(HttpCase):
    """The XLSX route as a user over HTTP: other company and unfinished profile are refused.

    Requirements: FR-DGQ-0057
    """

    def setUp(self):
        super().setUp()
        module = 'data_governance_quality_center_app'
        self.company_y = self.env['res.company'].create({'name': 'Route Company Y'})
        self.viewer = new_test_user(self.env, login='dgq_route_viewer', groups='base.group_user,%s.group_dgq_viewer' % module)
        self.outsider = new_test_user(self.env, login='dgq_route_outsider', groups='base.group_user,%s.group_dgq_viewer' % module)
        self.outsider.write({'company_ids': [(6, 0, self.company_y.ids)], 'company_id': self.company_y.id})
        profile = self.env['data.quality.profile']
        vals = {'model_id': self.env['ir.model']._get('res.partner').id, 'company_id': self.viewer.company_id.id}
        self.done = profile.create(vals)
        self.done.state = 'done'
        self.draft = profile.create(vals)

    def _get(self, login, profile):
        self.authenticate(login, login)
        return self.url_open('/data_quality/xlsx/profile/%s' % profile.id)

    def test_route_serves_a_finished_profile_to_a_viewer_only(self):
        """SC-DGQ-0057-1: a viewer of the profile's company gets the file; others and drafts do not."""
        response = self._get('dgq_route_viewer', self.done)
        self.assertEqual(response.status_code, 200)
        self.assertIn('spreadsheetml', response.headers['Content-Type'])
        self.assertEqual(self._get('dgq_route_viewer', self.draft).status_code, 422)
        self.assertEqual(self._get('dgq_route_outsider', self.done).status_code, 403)
        self.assertEqual(self._get('dgq_route_viewer', self.env['data.quality.profile'].browse(0)).status_code, 404)
