from odoo import models
from odoo.tools import SQL

# Shortest text that may match partly: shorter values would match almost everything.
MIN_PARTIAL_LENGTH = 3


def normalize(value) -> str:
    """Requirements: BR-DRD-0001, BR-DRD-0002 (the one way text is compared: no capitals, no outer spaces).

    An empty value gives an empty string, which never matches anything.
    """
    return value.strip().casefold() if value else ''


class DuplicateMatcher(models.AbstractModel):
    _name = 'duplicate.matcher'
    _description = 'Duplicate Matcher'

    def find_matches(self, record: models.BaseModel, rule: models.BaseModel) -> models.BaseModel:
        """Requirements: BR-DRD-0001, BR-DRD-0002, BR-DRD-0003, BR-DRD-0004.

        Return the other active records that match ``record`` under every condition of ``rule``:
        same company or shared, empty values never match. A SQL prefilter narrows the candidates
        and the Python comparison judges them.
        """
        # sudo: matches are found among all records of the type, not only those the saver may read
        Model = record.sudo().browse().with_context(active_test=True)
        conditions = rule.condition_ids
        if not rule.active or not conditions:
            return Model
        wanted = {c: self._value(record, c) for c in conditions}
        if any(not value for value in wanted.values()) or any(
                c.match_type == 'partial' and len(v) < MIN_PARTIAL_LENGTH
                for c, v in wanted.items()):
            return Model
        candidates = self._candidates(Model, record, wanted)
        return candidates.filtered(lambda other: all(
            self._values_match(c, wanted[c], self._value(other, c)) for c in conditions))

    def _value(self, record: models.BaseModel, condition: models.BaseModel):
        """Return the comparable value of a condition's field: a record id or normalised text."""
        value = record[condition.field_id.name]
        return value.id if condition.field_type == 'many2one' else normalize(value)

    def _values_match(self, condition: models.BaseModel, wanted, other) -> bool:
        """Judge one condition on two comparable values."""
        if not other:
            return False
        if condition.match_type == 'exact':
            return wanted == other
        shorter, longer = sorted((wanted, other), key=len)
        return len(shorter) >= MIN_PARTIAL_LENGTH and shorter in longer

    def _candidates(self, Model: models.BaseModel, record: models.BaseModel, wanted: dict) -> models.BaseModel:
        """Requirements: NFR-DRD-0001, BR-DRD-0004.

        Search the records that could match: same company or shared, and the SQL prefilter.
        """
        domain = [('id', '!=', record.id)]
        if record.company_id:
            domain.append(('company_id', 'in', [False, record.company_id.id]))
        for condition, value in wanted.items():
            if condition.field_type == 'many2one':
                domain.append((condition.field_id.name, '=', value))
        query = Model._search(domain)
        for condition, value in wanted.items():
            if condition.field_type != 'many2one':
                query.add_where(self._text_filter(query, condition, value))
        return Model.browse(query.get_result_ids())

    def _text_filter(self, query, condition: models.BaseModel, value: str) -> SQL:
        """Requirements: NFR-DRD-0001 (keep every record the Python judgement could accept: a superset)."""
        column = SQL("lower(btrim(%s))", query.table[condition.field_id.name])
        if condition.match_type == 'exact':
            return SQL("%s = %s", column, value)
        return SQL("(strpos(%s, %s) > 0 OR (%s <> '' AND strpos(%s, %s) > 0))",
                   column, value, column, value, column)
