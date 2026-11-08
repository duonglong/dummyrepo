import re
import time
from unittest.mock import patch

from odoo.sql_db import Cursor
from odoo.tests.common import tagged

from .acceptance_base import CHECK_CODES, AuditAcceptanceCase

MAX_RUN_SECONDS = 60  # NFR-OPA-0003: the bound of a full run on the test database
BIG_ROWS = 5_000_000  # well above the big-table threshold
PARTNER_KEY = 'A2:x_line.x_partner_id'
TECHNICAL_PREFIXES = ('pg_', 'information_schema', 'ir_')  # catalog and Odoo's technical tables


@tagged('post_install', '-at_install')
class TestEndToEnd(AuditAcceptanceCase):
    """Requirements: NFR-OPA-0001, NFR-OPA-0003, NFR-OPA-0004."""

    def _queries_of_a_run(self):
        """Start a run as the administrator and return it with the text of every query it sent."""
        sent = []
        original = Cursor.execute

        def record(cursor, query, *args, **kwargs):
            sent.append(str(getattr(query, 'code', query)))
            return original(cursor, query, *args, **kwargs)

        with patch.object(Cursor, 'execute', record):
            run = self.start_run()
        return run, sent

    def test_full_run_gives_every_check_a_line_and_none_fails(self):
        """NFR-OPA-0004: a full run holds one result per check, and no check ended failed."""
        run = self.start_run()
        self.assertEqual(run.state, 'done')
        for code in CHECK_CODES:
            self.assertIn(self.result(run, code).state, ('done', 'skipped'), code)

    def test_full_run_ends_in_under_sixty_seconds(self):
        """NFR-OPA-0003: a run with every check active takes less than 60 seconds."""
        started = time.monotonic()
        run = self.start_run()
        elapsed = time.monotonic() - started
        self.assertEqual(run.state, 'done')
        self.assertLess(elapsed, MAX_RUN_SECONDS)

    def test_run_never_counts_or_reads_a_business_table(self):
        """NFR-OPA-0001: no query of a run counts rows or selects from a business table."""
        self.make_x_line(rows=BIG_ROWS)
        _run, sent = self._queries_of_a_run()
        self.assertTrue(sent)
        for query in sent:
            lowered = query.lower()
            if re.search(r'\bcount\s*\(', lowered):
                tables = re.findall(r'\b(?:from|join)\s+"?([a-z_0-9.]+)"?', lowered)
                self.assertTrue(
                    all(table.startswith(TECHNICAL_PREFIXES) for table in tables),
                    'a run counted rows: %s' % query)
            self.assertFalse(
                re.search(r'\b(from|join)\s+"?x_line"?(?![\w])', lowered),
                'a run read a business table: %s' % query)

    def test_bad_database_gives_a_finding_and_a_clean_one_none(self):
        """NFR-OPA-0004: an unindexed many2one on a big table is found, an indexed one is not."""
        model = self.make_x_line(rows=BIG_ROWS)
        bad = self.start_run()
        self.assertTrue(self.findings(bad, 'A2').filtered(lambda f: f.identity_key == PARTNER_KEY))
        self.env['ir.model.fields'].search(
            [('model_id', '=', model.id), ('name', '=', 'x_partner_id')]).index = True
        self.set_reltuples('x_line', BIG_ROWS)
        self.env['perf.audit.run'].search([('state', '=', 'running')]).unlink()
        clean = self.start_run()
        self.assertFalse(self.findings(clean, 'A2').filtered(
            lambda f: f.identity_key == PARTNER_KEY and f.comparison_state != 'fixed'))
