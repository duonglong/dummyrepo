import resource

from .benchmark_common import (
    BENCHMARK_ROWS, RECORDS_PER_MILLION, SCAN_MEMORY_MB, SCAN_SECONDS_PER_MILLION_RECORDS, BenchmarkCase, benchmark)


class ScanBenchmarkCase(BenchmarkCase):
    """A scan of the benchmark contacts against ten standard rules."""

    def run_benchmark_scan(self):
        self.isolate('res.partner')
        self.create_partners(BENCHMARK_ROWS)
        rules = self.standard_rules()
        scan = self.make_scan(rules, models=['res.partner'])
        scan.action_start()
        self.drain(scan, max_runs=BENCHMARK_ROWS // 1000 + 100)
        self.assertEqual(scan.state, 'done', scan.state)
        return scan


@benchmark
class TestScanThroughputBenchmark(ScanBenchmarkCase):
    """A scan of one million records against ten standard rules ends within two hours.

    Run it with the command of the README; DGQ_BENCHMARK_ROWS sizes the run and the allowed time follows.

    Requirements: NFR-DGQ-0001
    """

    def test_scan_of_a_million_records_against_ten_rules(self):
        """NFR-DGQ-0001: the time of the scan, scaled to the size of the run, stays within the target."""
        started = self.now()
        scan = self.run_benchmark_scan()
        elapsed = self.now() - started
        allowed = SCAN_SECONDS_PER_MILLION_RECORDS * BENCHMARK_ROWS / RECORDS_PER_MILLION
        self.assertGreaterEqual(scan.records_scanned, BENCHMARK_ROWS)
        self.assertLessEqual(
            elapsed, allowed, "Scanning %s records took %.0f s, the target is %.0f s" % (BENCHMARK_ROWS, elapsed, allowed))


@benchmark
class TestScanMemoryBenchmark(ScanBenchmarkCase):
    """A scan keeps its memory under 500 MB whatever the size of the model.

    Requirements: NFR-DGQ-0003
    """

    def test_scan_memory_growth_stays_under_the_target(self):
        """NFR-DGQ-0003: the peak size of the process grows by less than the target during the scan."""
        peak_before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss  # kilobytes on Linux
        self.run_benchmark_scan()
        growth_mb = (resource.getrusage(resource.RUSAGE_SELF).ru_maxrss - peak_before) / 1024
        self.assertLess(growth_mb, SCAN_MEMORY_MB, "The scan grew the process by %.0f MB" % growth_mb)
