from odoo.exceptions import AccessError, UserError
from odoo.tests import Form

from .sfd_fixture import SfdFixture


class TestViewerAcceptance(SfdFixture):
    """Acceptance tests of T-003: the viewer screen and its lists.

    Requirements: BR-SFD-0001, FR-SFD-0001, FR-SFD-0002, FR-SFD-0003,
    FR-SFD-0004, FR-SFD-0005, FR-SFD-0007, FR-SFD-0010, NFR-SEC-0001,
    NFR-SEC-0002.
    Made-up fields of the scenarios are custom fields of x_sfd_a / x_sfd_b.
    The widget steps (empty message text, disabled export buttons, the depth
    box) are left to the build and its browser test; here the payload of
    get_dependency_map carries them (empty flag, depth_limit).
    """

    def _ir_field(self, model, name):
        return self.env['ir.model.fields']._get(model, name)

    def _form(self):
        return Form(self.env['sfd.dependency.viewer'].with_user(self.anna))

    def _dependents(self, payload):
        return {(d['model'], d['field']): d for d in payload['dependents']}

    def test_fields_narrow_to_chosen_model(self):
        """SC-SFD-0001-1 (FR-SFD-0001): choosing res.partner then country_id gives its map."""
        form = self._form()
        form.model_id = self.env['ir.model']._get('res.partner')
        form.field_id = self._ir_field('res.partner', 'country_id')
        record = form.save()
        self.assertEqual(record.field_id.model, 'res.partner')
        self.assertEqual(record.map_data['start']['field'], 'country_id')
        self.assertEqual(record.map_data['start']['model'], 'res.partner')

    def test_changing_model_clears_field(self):
        """SC-SFD-0001-1 (FR-SFD-0001): the field choice is cleared when the model changes."""
        form = self._form()
        form.model_id = self.env['ir.model']._get('res.partner')
        form.field_id = self._ir_field('res.partner', 'country_id')
        form.model_id = self.env['ir.model']._get('res.company')
        self.assertFalse(form.field_id)

    def test_no_map_before_a_model(self):
        """SC-SFD-0001-2 (FR-SFD-0001): with no model, no field is chosen and no map is shown."""
        record = self._form().save()
        self.assertFalse(record.model_id)
        self.assertFalse(record.field_id)
        self.assertFalse(record.map_data)

    def test_computed_dependents_on_same_and_other_model(self):
        """SC-SFD-0002-1 (FR-SFD-0002): computed dependents on model A and on model B, with stored flag."""
        deps = self._dependents(self.map('x_sfd_a', 'x_quantity'))
        subtotal = deps[('x_sfd_a', 'x_subtotal')]
        total = deps[('x_sfd_b', 'x_total')]
        self.assertEqual(subtotal['kind'], 'computed')
        self.assertTrue(subtotal['stored'])
        self.assertEqual(total['kind'], 'computed')
        self.assertTrue(total['stored'])

    def test_undeclared_computed_not_a_dependent(self):
        """SC-SFD-0002-2 (BR-SFD-0001): a computed field with no declared dependency is not listed.

        The onchange half of the scenario cannot be built as a fixture; an
        onchange is not a registry dependency, so it never appears.
        """
        deps = self._dependents(self.map('x_sfd_a', 'x_quantity'))
        self.assertNotIn(('x_sfd_a', 'x_note'), deps)
        self.assertEqual({f for (m, f) in deps if m == 'x_sfd_a'}, {'x_subtotal'})

    def test_related_dependent_through_relation(self):
        """SC-SFD-0003-1 (FR-SFD-0003): a related field through a relation is listed, marked related."""
        deps = self._dependents(self.map('res.partner', 'country_id'))
        row = deps[('x_sfd_b', 'x_partner_country_id')]
        self.assertEqual(row['kind'], 'related')

    def test_declared_path_and_method_shown(self):
        """SC-SFD-0004-1 (FR-SFD-0004): the row shows the declared path and the compute method."""
        deps = self._dependents(self.map('res.partner', 'parent_id'))
        row = deps[('res.partner', 'commercial_partner_id')]
        self.assertIn('parent_id.commercial_partner_id', row['path'])
        self.assertEqual(row['method'], '_compute_commercial_partner')
        total = self._dependents(self.map('x_sfd_a', 'x_quantity'))[('x_sfd_b', 'x_total')]
        self.assertIn('x_line_ids.x_quantity', total['path'])

    def test_related_path_shown_without_method(self):
        """SC-SFD-0004-2 (FR-SFD-0004): a related row shows its related path and no method."""
        row = self._dependents(self.map('res.partner', 'country_id'))[('x_sfd_b', 'x_partner_country_id')]
        self.assertEqual(row['related'], 'x_partner_id.country_id')
        self.assertIn('x_partner_id.country_id', row['path'])
        self.assertFalse(row['method'])

    def test_own_dependencies_of_computed_start(self):
        """SC-SFD-0005-1 (FR-SFD-0005): a computed start shows what it depends on."""
        payload = self.map('x_sfd_a', 'x_subtotal')
        self.assertEqual(set(payload['own_dependencies']), {'x_quantity', 'x_price'})

    def test_no_own_dependencies_for_plain_field(self):
        """SC-SFD-0005-2 (FR-SFD-0005): a plain start has no own-dependency section."""
        self.assertFalse(self.map('x_sfd_a', 'x_quantity').get('own_dependencies'))

    def test_empty_when_nothing_depends(self):
        """SC-SFD-0007-1 (FR-SFD-0007): no dependents gives the empty payload (message and disabled export follow from it)."""
        payload = self.map('x_sfd_a', 'x_reference')
        self.assertTrue(payload['empty'])
        self.assertEqual(payload['dependents'], [])
        self.assertEqual(payload['nodes'], [])
        self.assertFalse(payload['svg'])

    def test_not_empty_when_something_depends(self):
        """SC-SFD-0007-1 (FR-SFD-0007): the empty flag is false when a dependent exists."""
        self.assertFalse(self.map('x_sfd_a', 'x_quantity')['empty'])

    def test_each_users_own_limit_in_map(self):
        """SC-SFD-0010-1 (FR-SFD-0010, BR-SFD-0004): Anna's map uses 2, Ben's the default 5."""
        self.set_limit(self.anna, 2)
        self.assertEqual(self.map('x_sfd_a', 'x_c0', self.anna)['depth_limit'], 2)
        self.assertEqual(self.map('x_sfd_a', 'x_c0', self.ben)['depth_limit'], 5)

    def test_limit_change_redraws_and_is_kept(self):
        """SC-SFD-0010-2 (FR-SFD-0010): after setting 2 the map is redrawn with 2 and a new viewer shows 2.

        The input box of the widget is left to the build; the setter then
        get_dependency_map is the call sequence the design names for it.
        """
        before = self.map('x_sfd_a', 'x_c0')
        self.assertEqual(before['depth_limit'], 5)
        self.set_limit(self.anna, 2)
        after = self.map('x_sfd_a', 'x_c0')
        self.assertEqual(after['depth_limit'], 2)
        self.assertEqual(max(n['level'] for n in after['nodes']), 2)
        self.assertEqual(self.viewer().new({}).depth_limit, 2)

    def test_plain_user_refused(self):
        """NFR-SEC-0001: a user outside the Settings group cannot open the viewer or get a map."""
        with self.assertRaises(AccessError):
            self.map('x_sfd_a', 'x_quantity', self.plain_user)
        with self.assertRaises(AccessError):
            self.env['sfd.dependency.viewer'].with_user(self.plain_user).create({})

    def test_administrator_may_create_viewer(self):
        """NFR-SEC-0001: a Settings administrator can create the screen record."""
        self.assertTrue(self.env['sfd.dependency.viewer'].with_user(self.anna).create({}))

    def test_unknown_field_refused(self):
        """Design section 5: an unknown model or field name gives a clear refusal."""
        with self.assertRaises(UserError):
            self.map('x_sfd_a', 'x_does_not_exist')

    def test_viewer_changes_nothing(self):
        """NFR-SEC-0002: building a map creates no model, field or record."""
        self.env.flush_all()
        counts = [self.env[m].search_count([]) for m in ('ir.model', 'ir.model.fields', 'x_sfd_a', 'x_sfd_b')]
        self.map('x_sfd_a', 'x_quantity')
        self.map('res.partner', 'country_id')
        self.env.flush_all()
        self.assertEqual(
            counts, [self.env[m].search_count([]) for m in ('ir.model', 'ir.model.fields', 'x_sfd_a', 'x_sfd_b')])
