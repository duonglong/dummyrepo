from unittest.mock import patch

from .acceptance_base import AuditAcceptanceCase


def _statement(n, query='SELECT id FROM res_partner', total=10.0):
    return {'query_id': str(n), 'query': query, 'calls': n, 'total_time': total, 'mean_time': total / n}


class TestA6Own(AuditAcceptanceCase):
    """Requirements: FR-OPA-0012, FR-OPA-0003 (T-007)."""

    def _run(self, statements, extensions=('pg_stat_statements',), readable=True):
        probe = type(self.env['perf.audit.probe'])
        with patch.object(probe, 'installed_extensions', return_value=set(extensions)), \
                patch.object(probe, 'statements_readable', return_value=readable), \
                patch.object(probe, 'top_statements', side_effect=lambda limit: statements[:limit]) as top:
            run = self.start_run()
        return run, top

    def test_findings_name_models_and_figures(self):
        """FR-OPA-0012 / SC-OPA-0012-1: calls, total and mean time and the model of the table."""
        run, _top = self._run([_statement(4, 'SELECT * FROM res_partner p JOIN res_users u ON 1=1', 1234.5)])
        found = self.findings(run, 'A6')
        self.assertEqual(found.model_id.model, 'res.partner')
        self.assertEqual(found.severity, 'medium')
        self.assertEqual(found.evidence['calls'], 4)
        self.assertEqual(found.evidence['total_time_ms'], 1234.5)
        self.assertEqual(found.evidence['models'], ['res.partner', 'res.users'])

    def test_limit_is_the_top_threshold(self):
        """FR-OPA-0012 / SC-OPA-0012-2: the probe is asked for the top 20 and all of them are listed."""
        statements = [_statement(n) for n in range(1, 31)]
        run, top = self._run(statements)
        top.assert_called_once_with(20)
        self.assertEqual(len(self.findings(run, 'A6')), 20)

    def test_query_text_is_cut_to_500(self):
        """NFR-OPA-0005: a longer query text is stored as its first 500 characters."""
        long_query = 'SELECT ' + 'x' * 700
        run, _top = self._run([_statement(1, long_query)])
        self.assertEqual(self.findings(run, 'A6').query_text, long_query[:500])

    def test_skipped_naming_the_extension(self):
        """FR-OPA-0003: not installed, or installed but unreadable: skipped with the extension named."""
        run, top = self._run([], extensions=())
        self.assertEqual(self.result(run, 'A6').state, 'skipped')
        self.assertIn('pg_stat_statements', self.result(run, 'A6').reason)
        top.assert_not_called()
        run, _top = self._run([], readable=False)
        self.assertEqual(self.result(run, 'A6').state, 'skipped')
        self.assertIn('not readable', self.result(run, 'A6').reason)

    def test_clean_database_gives_done_without_finding(self):
        """FR-OPA-0012: readable and no statement: done, no finding."""
        run, _top = self._run([])
        self.assertEqual(self.result(run, 'A6').state, 'done')
        self.assertFalse(self.findings(run, 'A6'))
