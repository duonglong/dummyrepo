from types import SimpleNamespace

from markupsafe import Markup

from odoo import api, fields, models
from odoo.exceptions import ValidationError

from ..services import explain, scan as scan_service, validation
from .rule import DEFINITION_FIELDS

# How many records the rule test takes from the model when the manager picks none.
SAMPLE_SIZE = 20
STATUS_CLASSES = {
    validation.PASS: 'text-success',
    validation.FAIL: 'text-danger',
    validation.NOT_APPLICABLE: 'text-muted',
}


class DataQualityRuleTest(models.Model):  # sdd-allow: model-split T-008 lists the rule test in its own file
    """The test of a rule on sample records, shown in the rule form.

    Requirements: FR-DGQ-0012
    """
    _inherit = 'data.quality.rule'

    test_res_ids = fields.Json(
        string='Records to Test', store=False, copy=False,
        help="The records to check. Leave empty to check the first %s records of the model." % SAMPLE_SIZE)
    test_results = fields.Json(
        string='Test Results', compute='_compute_test_results', store=False)
    test_report = fields.Html(
        string='Test Report', compute='_compute_test_report', store=False, sanitize=False)

    @api.depends(*DEFINITION_FIELDS, 'advice', 'test_res_ids')
    @api.depends_context('lang', 'uid')
    def _compute_test_results(self):
        """Check the rule as it stands, even unsaved, on the picked or sampled records.

        Requirements: FR-DGQ-0012
        """
        for rule in self:
            rule.test_results = rule._dgq_test()

    @api.depends('test_results')
    def _compute_test_report(self):
        """The test results as a table: record, result and explanation."""
        for rule in self:
            results = rule.test_results or {}
            if results.get('not_run_reason'):
                rule.test_report = Markup('<p class="text-warning">{}</p>').format(results['not_run_reason'])
                continue
            labels = {
                validation.PASS: self.env._("Pass"),
                validation.FAIL: self.env._("Fail"),
                validation.NOT_APPLICABLE: self.env._("Not applicable"),
            }
            rows = Markup('').join(
                Markup('<tr><td>{}</td><td class="{}">{}</td><td class="text-break" style="white-space: pre-line">{}</td></tr>').format(
                    entry['name'], STATUS_CLASSES[entry['status']], labels[entry['status']], entry['explanation'] or '')
                for entry in results.get('records', []))
            rule.test_report = Markup('<table class="table table-sm"><tbody>{}</tbody></table>').format(rows) if rows else False

    def _dgq_incomplete_reason(self):
        """Why the rule on screen cannot be checked yet (a model, a field or a setting is missing or wrong), or False."""
        self.ensure_one()
        try:
            for check in self._constraint_methods:
                check(self)
        except ValidationError as error:
            return error.args[0]
        return scan_service.not_run_reason(self.env, self) or False

    def _dgq_test_records(self):
        """The records of the test: the picked ones, else a sample; only those the user may read."""
        self.ensure_one()
        Model = self.env[self.model_name]
        if not Model.has_access('read'):
            return Model
        picked = list(dict.fromkeys(
            res_id for res_id in self.test_res_ids or [] if isinstance(res_id, int)))
        if not picked:
            return Model.search([], limit=SAMPLE_SIZE)
        records = Model.browse(picked).exists()._filtered_access('read')
        return records.sorted(lambda record: picked.index(record.id))

    def _dgq_test(self):
        """The result of the test: ``{'not_run_reason': text or False, 'records': [entry, ...]}``.

        An entry holds the record's id and name, its result (pass, fail or not_applicable) and,
        for a failure, the explanation with the advice of the rule. Nothing is written.
        """
        self.ensure_one()
        if not self.model_id or not self.rule_type:
            return {'not_run_reason': False, 'records': []}
        reason = self._dgq_incomplete_reason()
        if reason:
            return {'not_run_reason': reason, 'records': []}
        records = self._dgq_test_records()
        # The companies of a form that is not saved yet are placeholders of the saved ones.
        scope = SimpleNamespace(company_ids=self.company_ids._origin, applies_domain=self.applies_domain)
        # sudo: like a scan, the check reads the values of the rule's fields; sensitive values are never quoted
        applicable = scan_service.applicable_records(scope, records.sudo())
        outcomes = {
            record_id: outcome
            for (record_id, _rule_id), outcome in validation.check(self, applicable).items()}
        entries = []
        for record in records:
            outcome = outcomes.get(record.id) or validation.Outcome(validation.NOT_APPLICABLE)
            failed = outcome.status == validation.FAIL
            entries.append({
                'res_id': record.id,
                'name': record.display_name,
                'status': outcome.status,
                'explanation': explain.render(
                    self.env, outcome.code, outcome.params, self.advice) if failed else False,
            })
        return {'not_run_reason': False, 'records': entries}
