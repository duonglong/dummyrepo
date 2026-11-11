import time

from odoo.tests.common import tagged

from .common import AttachmentManagementCase

ROWS_PER_MODEL = 20_000
RECORDS_PER_MODEL = 50
FIRST_PAGE_SECONDS = 2
PAGE_SIZE = 80
WEB_COUNT_LIMIT = 10_001
OWN_UPLOAD_EVERY = 20
READABLE_MODELS = ('res.partner', 'res.partner.category', 'res.partner.industry')
HIDDEN_MODELS = ('ir.config_parameter', 'ir.module.category')
LIST_SPEC = {'name': {}, 'res_model_id': {}, 'record_label': {}, 'create_uid': {}, 'file_type_group': {}}


@tagged('post_install', '-at_install')
class TestSearchPerformance(AttachmentManagementCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env['res.partner.category'].create([{'name': f'Perf tag {n}'} for n in range(RECORDS_PER_MODEL)])
        cls.env['res.partner.industry'].create([{'name': f'Perf industry {n}'} for n in range(RECORDS_PER_MODEL)])
        cls.env['res.partner'].create([{'name': f'Perf partner {n}'} for n in range(RECORDS_PER_MODEL)])
        for index, model in enumerate(READABLE_MODELS + HIDDEN_MODELS):
            cls.env.cr.execute(f'SELECT array_agg(id) FROM {cls.env[model]._table}')
            record_ids = cls.env.cr.fetchone()[0]
            cls.env.cr.execute("""
                INSERT INTO ir_attachment (name, type, mimetype, res_model, res_id, res_model_id,
                                           file_type_group, create_uid, create_date)
                SELECT 'perf-' || %(index)s || '-' || n, 'binary',
                       CASE WHEN mod(n, 2) = 0 THEN 'application/pdf' ELSE 'image/png' END,
                       %(model)s, (%(record_ids)s::int[])[1 + mod(n, %(count)s)], %(model_id)s,
                       CASE WHEN mod(n, 2) = 0 THEN 'pdf' ELSE 'image' END,
                       CASE WHEN mod(n, %(own_every)s) = 0 THEN %(own_uid)s ELSE %(other_uid)s END,
                       now() - ((n * 5 + %(index)s) || ' seconds')::interval
                  FROM generate_series(1, %(rows)s) n
            """, {
                'index': index, 'model': model, 'record_ids': record_ids, 'count': len(record_ids),
                'model_id': cls.env['ir.model']._get_id(model), 'own_every': OWN_UPLOAD_EVERY,
                'own_uid': cls.am_user.id, 'other_uid': cls.am_other_user.id, 'rows': ROWS_PER_MODEL,
            })
        # A database of this size has planner statistics; rows inserted in this transaction get none until analyzed.
        cls.env.cr.execute('ANALYZE ir_attachment')
        cls.env.invalidate_all()
        cls.action = cls.env.ref('attachment_management.action_attachment_central')
        cls.menu_domain = [('type', '=', 'binary'), ('url', '=', False)]

    def _list_seconds(self, domain):
        """Time what the list does on opening: the page and the count the web client asks for."""
        attachments = self.env['ir.attachment'].with_user(self.am_user)
        started = time.monotonic()
        result = attachments.web_search_read(
            domain, LIST_SPEC, limit=PAGE_SIZE, order='create_date desc', count_limit=WEB_COUNT_LIMIT)
        elapsed = time.monotonic() - started
        self.assertTrue(result['records'])
        return elapsed

    def test_menu_opens_on_my_uploads(self):
        """Requirements: NFR-AM-0001. The menu opens with the My uploads filter on (design contingency, section 8)."""
        context = self.action.context
        self.assertIn('search_default_my_uploads', context)

    def test_menu_opening_within_two_seconds(self):
        """Requirements: NFR-AM-0001. The menu as opened (My uploads on) over 100,000 attachments of 5 kinds of record.

        It cannot reach the browser: it times the server calls the list makes.
        """
        domain = self.menu_domain + [('create_uid', '=', self.am_user.id)]
        self.assertLess(self._list_seconds(domain), FIRST_PAGE_SECONDS)

    def test_file_type_filter_within_two_seconds(self):
        """Requirements: NFR-AM-0001. A file-type filter over every upload of every kind of record."""
        domain = self.menu_domain + [('file_type_group', '=', 'pdf')]
        self.assertLess(self._list_seconds(domain), FIRST_PAGE_SECONDS)

    def test_owner_filter_within_two_seconds(self):
        """Requirements: NFR-AM-0001. An owner filter on someone else's uploads, over every kind of record."""
        domain = self.menu_domain + [('create_uid', '=', self.am_other_user.id)]
        self.assertLess(self._list_seconds(domain), FIRST_PAGE_SECONDS)

    def test_name_search_within_two_seconds(self):
        """Requirements: NFR-AM-0001. A search on the file name that matches files of every kind of record."""
        domain = self.menu_domain + [('name', 'ilike', 'perf-')]
        self.assertLess(self._list_seconds(domain), FIRST_PAGE_SECONDS)

    def test_kind_of_record_filter_within_two_seconds(self):
        """Requirements: NFR-AM-0001. A kind-of-record filter."""
        domain = self.menu_domain + [('res_model_id', '=', self.env['ir.model']._get_id('res.partner'))]
        self.assertLess(self._list_seconds(domain), FIRST_PAGE_SECONDS)

    def test_group_by_record_within_two_seconds(self):
        """Requirements: NFR-AM-0001. The Record group-by, timed as the design says, on the opened menu."""
        domain = self.menu_domain + [('create_uid', '=', self.am_user.id)]
        attachments = self.env['ir.attachment'].with_user(self.am_user)
        started = time.monotonic()
        groups = attachments.formatted_read_group(domain, ['record_label'], ['__count'])
        self.assertTrue(groups)
        self.assertLess(time.monotonic() - started, FIRST_PAGE_SECONDS)
