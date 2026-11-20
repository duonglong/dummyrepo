from odoo import api, models
from odoo.fields import Domain
from odoo.orm.domains import DomainAnd


class ProcurementReportGuard(models.AbstractModel):
    """Leave an aggregate empty when the grouping would total figures that must not be added up.

    Requirements: FR-SPM-0102, FR-SPM-0103
    A report model lists, in ``_spm_guarded_aggregates``, the aggregates that only mean something
    inside one value of a field (a savings total inside one baseline kind, a rate inside one
    stage). Both read paths of the ORM, the list/graph one and the pivot one, blank them when the
    grouping or the domain does not pin that field.
    """
    _name = 'procurement.report.guard'
    _description = 'Report Aggregate Guard'

    _spm_guarded_aggregates = {}

    @api.model
    def _spm_blanked(self, domain, groupby, aggregates):
        """Return the positions in ``aggregates`` that must come back empty for this grouping."""
        grouped = {spec.split(':')[0] for spec in groupby}
        pinned = self._spm_pinned_fields(domain)
        blanked = []
        for at, spec in enumerate(aggregates):
            rule = self._spm_guarded_aggregates.get(spec)
            if rule and not rule['grouped_by_needed'](grouped, pinned):
                blanked.append(at)
        return blanked

    @api.model
    def _spm_pinned_fields(self, domain):
        """Return the fields the domain fixes to one value (a top-level equality or one-value 'in')."""
        parsed = Domain(domain)
        parts = parsed.children if isinstance(parsed, DomainAnd) else (parsed,)
        pinned = set()
        for part in parts:
            if not hasattr(part, 'field_expr'):
                continue
            value = part.value
            if part.operator == '=' or (part.operator == 'in' and len(value) == 1):
                pinned.add(part.field_expr)
        return pinned

    @api.model
    def _read_group(self, domain, groupby=(), aggregates=(), having=(), offset=0, limit=None, order=None):
        """Blank the guarded aggregates the grouping cannot answer.

        Requirements: FR-SPM-0102, FR-SPM-0103
        """
        rows = super()._read_group(domain, groupby, aggregates, having, offset, limit, order)
        blanked = {len(groupby) + at for at in self._spm_blanked(domain, groupby, aggregates)}
        if not blanked:
            return rows
        return [tuple(None if at in blanked else value for at, value in enumerate(row)) for row in rows]

    @api.model
    def _read_grouping_sets(self, domain, grouping_sets, aggregates=(), order=None):
        """Blank the guarded aggregates of each grouping set (the pivot reads through here).

        Requirements: FR-SPM-0102, FR-SPM-0103
        """
        result = super()._read_grouping_sets(domain, grouping_sets, aggregates, order)
        for groupby, rows in zip(grouping_sets, result):
            blanked = {len(groupby) + at for at in self._spm_blanked(domain, groupby, aggregates)}
            if blanked:
                rows[:] = [tuple(None if at in blanked else value for at, value in enumerate(row)) for row in rows]
        return result
