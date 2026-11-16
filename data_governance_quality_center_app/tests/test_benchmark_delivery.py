import importlib

from odoo.modules import get_module_path
from odoo.tests import tagged

from .common import DGQTestCase

BENCHMARK_TAG = 'dgq_benchmark'
BENCHMARK_CLASSES = {
    'NFR-DGQ-0001': ('test_benchmark_scan', 'TestScanThroughputBenchmark'),
    'NFR-DGQ-0002': ('test_benchmark_save', 'TestSaveTimeBenchmark'),
    'NFR-DGQ-0003': ('test_benchmark_scan', 'TestScanMemoryBenchmark'),
    'NFR-DGQ-0004': ('test_benchmark_dashboard', 'TestDashboardBenchmark'),
}


@tagged('post_install', '-at_install')
class TestBenchmarkDelivery(DGQTestCase):
    """The app delivers a benchmark for each performance target, kept out of the default run.

    Requirements: NFR-DGQ-0001, NFR-DGQ-0002, NFR-DGQ-0003, NFR-DGQ-0004
    """

    def benchmark_classes(self):
        return {
            requirement: getattr(importlib.import_module('%s.%s' % (__package__, module)), name)
            for requirement, (module, name) in BENCHMARK_CLASSES.items()}

    def test_bug_b_0214_every_performance_target_has_a_benchmark(self):
        """Regression test for B-0214: no benchmark existed for the four performance targets.

        Requirements: NFR-DGQ-0001, NFR-DGQ-0002, NFR-DGQ-0003, NFR-DGQ-0004
        """
        for requirement, test_class in self.benchmark_classes().items():
            with self.subTest(requirement=requirement):
                self.assertTrue(
                    [name for name in dir(test_class) if name.startswith('test_')],
                    "%s has no benchmark test" % requirement)
                self.assertIn(requirement, test_class.__doc__)

    def test_bug_b_0214_benchmarks_run_only_when_asked_for_by_tag(self):
        """Regression test for B-0214: a million-row run never slows the default test run.

        Requirements: NFR-DGQ-0001
        """
        for requirement, test_class in self.benchmark_classes().items():
            with self.subTest(requirement=requirement):
                self.assertIn(BENCHMARK_TAG, test_class.test_tags)
                for default_tag in ('standard', 'at_install'):
                    self.assertNotIn(default_tag, test_class.test_tags)

    def test_bug_b_0214_the_run_command_is_documented(self):
        """Regression test for B-0214: the README gives the command that runs the benchmark.

        Requirements: NFR-DGQ-0001
        """
        with open(get_module_path('data_governance_quality_center_app') + '/README.md', encoding='utf-8') as readme:
            self.assertIn(
                '--test-tags %s/data_governance_quality_center_app' % BENCHMARK_TAG, readme.read())
