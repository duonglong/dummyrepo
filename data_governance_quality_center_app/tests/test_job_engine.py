import time
from datetime import timedelta
from unittest.mock import patch

from odoo import fields, models
from odoo.orm.model_classes import add_to_registry
from odoo.tests import tagged

from ..services import jobs
from .common import DGQTestCase

MODULE = 'data_governance_quality_center_app'


def define_test_job():
    """Build the concrete job kind used by these tests, outside the module's own models.

    The definition is taken out of the module's model list right away, so a registry
    loaded later never carries it.
    """
    class DgqTestJob(models.Model):
        _name = 'dgq.test.job'
        _inherit = ['data.quality.job.mixin']
        _description = 'DGQ test job'
        _module = MODULE

        batches = fields.Integer(default=3)
        fail_at = fields.Integer()

        def _dgq_run_batch(self):
            batch = self.done_count + 1
            if batch == self.fail_at:
                raise ValueError("boom")
            return 1, max(self.batches - batch, 0)

    models.MetaModel._module_to_models__[MODULE].remove(DgqTestJob)
    return DgqTestJob


@tagged('post_install', '-at_install')
class TestJobEngine(DGQTestCase):
    """Requirements: NFR-DGQ-0002, NFR-DGQ-0003, NFR-DGQ-0005"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        registry = cls.registry
        cls.TestJob = define_test_job()
        add_to_registry(registry, cls.TestJob)
        registry._setup_models__(cls.env.cr)
        registry.init_models(cls.env.cr, ['dgq.test.job'], {'module': MODULE})
        cls.addClassCleanup(registry.models.pop, 'dgq.test.job', None)
        cls.Job = cls.env['dgq.test.job']

    def setUp(self):
        super().setUp()
        # A test cannot commit or roll back its cursor: the runner's commit points are
        # replaced by recorders; the commit itself is Odoo's own and is not under test.
        cron = type(self.env['ir.cron'])
        commit = patch.object(cron, '_commit_progress', return_value=float('inf'))
        rollback = patch.object(cron, '_rollback_progress')
        self.commit_progress = commit.start()
        self.rollback_progress = rollback.start()
        self.addCleanup(commit.stop)
        self.addCleanup(rollback.stop)

    def _job(self, **vals):
        return self.Job.create(vals)

    def test_start_queues_and_records_user(self):
        """Starting returns at once: the job is only queued, with its requester."""
        job = self._job()
        job.action_start()
        self.assertEqual(job.state, 'queued')
        self.assertEqual(job.user_id, self.env.user)

    def test_run_processes_in_batches_to_done(self):
        """The runner processes every batch and ends done with full progress."""
        job = self._job(batches=3)
        job.action_start()
        jobs.run_next_job(self.env, 'dgq.test.job')
        self.assertEqual((job.state, job.done_count, job.progress), ('done', 3, 100.0))
        self.assertTrue(job.heartbeat)

    def test_oldest_queued_job_runs_first_one_at_a_time(self):
        """A runner takes only the oldest queued job; the other waits."""
        first, second = self._job(batches=1), self._job(batches=1)
        first.action_start()
        second.action_start()
        second.date_queued = first.date_queued + timedelta(seconds=5)
        jobs.run_next_job(self.env, 'dgq.test.job')
        self.assertEqual((first.state, second.state), ('done', 'queued'))

    def test_failure_rolls_back_batch_and_keeps_checkpoint(self):
        """A batch that raises fails the job; earlier batches stay and it can resume."""
        job = self._job(batches=3, fail_at=3)
        job.action_start()
        jobs.run_next_job(self.env, 'dgq.test.job')
        self.assertEqual((job.state, job.done_count, job.error_message), ('failed', 2, 'boom'))
        self.rollback_progress.assert_called_once()
        job.fail_at = 0
        job.action_resume()
        self.assertEqual(job.state, 'queued')
        jobs.run_next_job(self.env, 'dgq.test.job')
        self.assertEqual((job.state, job.done_count), ('done', 3))

    def test_stops_when_time_is_short_and_continues_later(self):
        """With no time left the job goes back to queued and the next run finishes it."""
        job = self._job(batches=3)
        job.action_start()
        self.commit_progress.return_value = 0.0
        jobs.run_next_job(self.env, 'dgq.test.job')
        self.assertEqual((job.state, job.done_count), ('queued', 1))
        self.commit_progress.return_value = float('inf')
        jobs.run_next_job(self.env, 'dgq.test.job')
        self.assertEqual((job.state, job.done_count), ('done', 3))

    def test_stale_running_job_is_failed(self):
        """A running job without a recent heartbeat is set failed by the next run."""
        job = self._job(state='running', heartbeat=fields.Datetime.now() - timedelta(minutes=31))
        fresh = self._job(state='running', heartbeat=fields.Datetime.now())
        jobs.run_next_job(self.env, 'dgq.test.job')
        self.assertEqual((job.state, fresh.state), ('failed', 'running'))
        self.assertIn('server restart', job.error_message)

    def test_cancel_and_wrong_state(self):
        """A queued job can be cancelled; a finished job cannot be started or cancelled."""
        job = self._job()
        job.action_start()
        job.action_cancel()
        self.assertEqual(job.state, 'cancelled')
        done = self._job(state='done')
        with self.assertRaises(Exception):
            done.action_cancel()
        with self.assertRaises(Exception):
            done.action_start()

    def test_batch_reads_never_exceed_batch_size(self):
        """A job kind receives one batch per call, never the whole model at once."""
        job = self._job(batches=2)
        job.action_start()
        calls = []
        original = self.TestJob._dgq_run_batch

        def spy(this):
            calls.append(this.done_count)
            return original(this)

        with patch.object(self.TestJob, '_dgq_run_batch', spy):
            jobs.run_next_job(self.env, 'dgq.test.job')
        self.assertEqual(calls, [0, 1])
