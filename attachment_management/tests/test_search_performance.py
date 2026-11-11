import time

from odoo.tests.common import tagged

from .common import AttachmentManagementCase

ROWS = 100_000
FIRST_PAGE_SECONDS = 2
PAGE_SIZE = 80


@tagged('post_install', '-at_install')
class TestSearchPerformance(AttachmentManagementCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner_model = cls.env['ir.model']._get_id('res.partner')
        partner = cls.env['res.partner'].create({'name': 'Perf Partner AM'})
        cls.env.cr.execute("""
            INSERT INTO ir_attachment (name, type, mimetype, res_model, res_id, res_model_id,
                                       file_type_group, create_uid, create_date)
            SELECT 'perf-' || n, 'binary',
                   CASE WHEN mod(n, 2) = 0 THEN 'application/pdf' ELSE 'image/png' END,
                   'res.partner', %s, %s,
                   CASE WHEN mod(n, 2) = 0 THEN 'pdf' ELSE 'image' END,
                   %s, now() - (n || ' seconds')::interval
              FROM generate_series(1, %s) n
        """, (partner.id, cls.partner_model, cls.am_user.id, ROWS))
        cls.env.invalidate_all()

    def _first_page_seconds(self, domain):
        attachments = self.env['ir.attachment'].with_user(self.am_user)
        started = time.monotonic()
        attachments.search(domain, limit=PAGE_SIZE, order='create_date desc')
        attachments.search_count(domain, limit=PAGE_SIZE * 10)
        return time.monotonic() - started

    def test_first_page_within_two_seconds(self):
        """Requirements: NFR-AM-0001. First page of the search with and without a filter on 100,000 attachments."""
        base = [('type', '=', 'binary'), ('url', '=', False), ('name', '=like', 'perf-%')]
        for extra in ([('res_model_id', '=', self.partner_model)], [('file_type_group', '=', 'pdf')]):
            self.assertLess(self._first_page_seconds(base + extra), FIRST_PAGE_SECONDS, extra)
