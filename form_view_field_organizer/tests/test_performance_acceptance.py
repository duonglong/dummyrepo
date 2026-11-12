import time

from lxml import etree

from .common import FvfCommon

LIMIT_SECONDS = 0.5


class TestPerformanceAcceptance(FvfCommon):
    """Acceptance tests of T-007: forms stay fast with a published layout.

    Requirements: NFR-PERF-0001, FR-FVF-0012 (SC-FVF-0012-1).
    The form is read as a plain user does, through get_views of res.partner,
    cold (view cache cleared) and warm (cache filled).  The extra time of a
    published 30-line layout must stay under 0.5 s and a warm read must run
    no extra query.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner_model = cls.env['ir.model']._get('res.partner')
        cls.view = cls.env.ref('base.view_partner_form')

    def _published_layout(self):
        layout = self.env['fvf.layout'].create({
            'model_id': self.partner_model.id,
            'view_id': self.view.id,
        })
        free = layout.line_ids.filtered(lambda l: not l.is_required)
        self.assertGreaterEqual(len(layout.line_ids), 25, "the Contact form must offer about 30 lines")
        hidden, moved = free[5:], free[:5]
        hidden.write({'hidden_group_ids': [(4, self.env.ref('base.group_user').id)]})
        section = self.env['fvf.layout.section'].create({'layout_id': layout.id, 'name': 'Perf section'})
        moved.write({'section_id': section.id})
        layout.action_publish()
        return layout

    def _get_views(self):
        partners = self.env['res.partner'].with_user(self.user_ben).with_company(self.company_be)
        return partners.get_views([(False, 'form')])

    def _timed(self, cold):
        if cold:
            self.env.transaction.invalidate_ormcache('templates')
        start = time.perf_counter()
        self._get_views()
        return time.perf_counter() - start

    def _queries_of_warm_read(self):
        self._get_views()
        before = self.env.cr.sql_log_count
        self._get_views()
        return self.env.cr.sql_log_count - before

    def test_cold_read_with_published_layout_is_less_than_half_a_second_slower(self):
        """NFR-PERF-0001: cold read with a ~30-line layout adds under 0.5 s."""
        self._timed(cold=True)  # lazy loads
        without = min(self._timed(cold=True) for _i in range(3))
        self._published_layout()
        with_layout = min(self._timed(cold=True) for _i in range(3))
        self.assertLess(with_layout - without, LIMIT_SECONDS)

    def test_warm_read_with_published_layout_is_less_than_half_a_second_slower(self):
        """NFR-PERF-0001: warm read with a ~30-line layout adds under 0.5 s."""
        self._get_views()
        without = min(self._timed(cold=False) for _i in range(3))
        self._published_layout()
        self._get_views()
        with_layout = min(self._timed(cold=False) for _i in range(3))
        self.assertLess(with_layout - without, LIMIT_SECONDS)

    def test_layout_really_changes_the_form_being_timed(self):
        """NFR-PERF-0001 guard (SC-FVF-0012-1: users see the layout): the timed form is the organized one."""
        before = etree.fromstring(self._get_views()['views']['form']['arch'])
        self._published_layout()
        after = etree.fromstring(self._get_views()['views']['form']['arch'])
        self.assertNotEqual(etree.tostring(before), etree.tostring(after))
        self.assertTrue(after.xpath('.//div[contains(@class, "o_fvf_section")]'))

    def test_warm_read_runs_no_extra_query_with_a_layout(self):
        """NFR-PERF-0001: warm query count with a published layout equals the count without."""
        without = self._queries_of_warm_read()
        self._published_layout()
        with_layout = self._queries_of_warm_read()
        self.assertEqual(with_layout, without)

    def test_model_without_layout_adds_an_empty_cache_token(self):
        """NFR-PERF-0001: a model with no published layout adds a cache token that is the empty tuple."""
        Layout = self.env['fvf.layout']
        self.assertEqual(tuple(Layout._fvf_cache_token('res.partner')), ())
        self._published_layout()
        self.assertNotEqual(tuple(Layout._fvf_cache_token('res.partner')), ())
        self.assertEqual(tuple(Layout._fvf_cache_token('res.users.log')), ())
