from .benchmark_common import SAVE_SECONDS, BenchmarkCase, benchmark

SAVES = 1000


@benchmark
class TestSaveTimeBenchmark(BenchmarkCase):
    """A save of a record on a model watched by an event automation stays under half a second.

    Requirements: NFR-DGQ-0002
    """

    def test_a_thousand_saves_on_a_watched_model(self):
        """NFR-DGQ-0002: no create or write of a user on a watched model takes longer than the target."""
        rule = self.make_rule('res.partner', 'required', ['email'])
        self.make_automation(trigger='event', event='both', rules=[rule], models=['res.partner'])
        Partner = self.env['res.partner']
        slowest = 0.0
        for number in range(SAVES):
            started = self.now()
            partner = Partner.create({'name': 'DGQ save %s' % number, 'company_id': self.cx.id})
            partner.write({'phone': '+32 2 555 %s' % number})
            slowest = max(slowest, self.now() - started)
        self.assertLess(slowest, SAVE_SECONDS, "The slowest of %s saves took %.3f s" % (SAVES, slowest))
