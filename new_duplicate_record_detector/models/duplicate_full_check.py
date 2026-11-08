from collections import defaultdict
from collections.abc import Iterable

from odoo import Command, models

from .duplicate_matcher import MIN_PARTIAL_LENGTH


class DuplicateFullCheck(models.AbstractModel):
    _name = 'duplicate.full.check'
    _description = 'Duplicate Full Check'

    def replace_open_sets(self, rule: models.BaseModel) -> int:
        """Requirements: FR-DRD-0005, FR-DRD-0006, BR-DRD-0004, NFR-DRD-0002.

        Compare all active records of the rule's type and replace the open sets of the rule with
        one set per group of matching records. Merged sets and sets marked not a duplicate stay.
        Return the number of sets recorded.
        """
        # sudo: the check looks at every record of the type and writes sets whoever asked for it
        rule = rule.sudo()
        records = self.env[rule.model_name].sudo().search([])  # sudo: every record of the type, any company
        company_of = {record.id: record.company_id.id for record in records}
        clusters = self._clusters(
            self._edges(rule, records, self._apart_groups(rule)), company_of)
        names = {record.id: record.display_name for record in records}
        Group = self.env['duplicate.group'].sudo()  # sudo: sets are written for a manager without all rights
        Group.search([('rule_id', '=', rule.id), ('state', '=', 'open')]).unlink()
        Group.create([{
            'rule_id': rule.id, 'model_name': rule.model_name, 'company_id': company,
            'line_ids': [Command.create({
                'res_model': rule.model_name, 'res_id': res_id,
                'name': names[res_id], 'company_id': company_of[res_id],
            }) for res_id in sorted(members)],
        } for company, members in clusters])
        return len(clusters)

    def _apart_groups(self, rule: models.BaseModel) -> dict:
        """Requirements: FR-DRD-0006, FR-DRD-0008.

        Return, for each record, the ids of the sets already decided (merged or marked not a
        duplicate) that hold it: two records of one such set are not reported together again.
        """
        # sudo: decided sets of any company count
        lines = self.env['duplicate.group.line'].sudo().search([
            ('group_id.rule_id', '=', rule.id), ('group_id.state', 'in', ('merged', 'not_duplicate'))])
        apart = defaultdict(set)
        for line in lines:
            apart[line.res_id].add(line.group_id.id)
        return apart

    def _edges(self, rule: models.BaseModel, records: models.BaseModel, apart: dict) -> list:
        """Requirements: BR-DRD-0003, BR-DRD-0004.

        Return the pairs of records that match under every condition, are of one company or
        shared, are not one the other's own company, and were not marked as not duplicates of
        each other.
        """
        matcher = self.env['duplicate.matcher']
        conditions = rule.condition_ids
        values = {
            record.id: {c: matcher._value(record, c) for c in conditions} for record in records}
        company = {record.id: record.company_id.id for record in records}
        above = {record.id: set(matcher.ancestor_ids(record)) for record in records} \
            if 'parent_id' in records._fields else defaultdict(set)
        values = {
            res_id: wanted for res_id, wanted in values.items()
            if self._is_comparable(conditions, wanted)}
        lead = self._lead_condition(conditions)
        index = self._index(lead, values)
        edges = set()
        for res_id, wanted in values.items():
            for other in self._candidate_ids(lead, wanted[lead], index):
                if other == res_id or apart[res_id] & apart[other]:
                    continue
                if other in above[res_id] or res_id in above[other]:
                    continue
                if company[res_id] and company[other] and company[res_id] != company[other]:
                    continue
                if all(matcher._values_match(c, wanted[c], values[other][c]) for c in conditions):
                    edges.add((min(res_id, other), max(res_id, other)))
        return list(edges)

    @staticmethod
    def _lead_condition(conditions: models.BaseModel) -> models.BaseModel:
        """Return the condition whose index finds the pairs: an exact one if any, else the first.

        Every matching pair satisfies the lead condition, so one index finds all of them.
        """
        return next((c for c in conditions if c.match_type == 'exact'), conditions[0])

    @staticmethod
    def _is_comparable(conditions: models.BaseModel, wanted: dict) -> bool:
        """Tell whether a record has a value to compare under every condition (empty never matches)."""
        return all(
            wanted[c] and not (c.match_type == 'partial' and len(wanted[c]) < MIN_PARTIAL_LENGTH)
            for c in conditions)

    @staticmethod
    def _trigrams(text: str) -> set:
        """Return the 3-character pieces of a text: a text holding another shares at least one."""
        return {text[i:i + MIN_PARTIAL_LENGTH] for i in range(len(text) - MIN_PARTIAL_LENGTH + 1)}

    def _index(self, condition: models.BaseModel, values: dict) -> dict:
        """Index the records by value (exact) or by 3-character piece (partial) of one condition."""
        index = defaultdict(set)
        for res_id, wanted in values.items():
            keys = self._trigrams(wanted[condition]) if condition.match_type == 'partial' \
                else (wanted[condition],)
            for key in keys:
                index[key].add(res_id)
        return index

    def _candidate_ids(self, condition: models.BaseModel, value, index: dict) -> Iterable[int]:
        """Return the ids that could match a value on a condition: a superset of the real matches.

        For a partial condition these are the records holding the value (sharing all its pieces),
        so a pair is found from the side of the contained value.
        """
        if condition.match_type == 'exact':
            return index.get(value, ())
        return set.intersection(*(index.get(piece, set()) for piece in self._trigrams(value)))

    def _clusters(self, edges: list, company_of: dict) -> list:
        """Requirements: BR-DRD-0004, BR-DRD-0005.

        Group the linked records into sets with union-find, one pass per company: a record shared
        by all companies joins the set of each company, and two companies never share a set.
        Return (company id, member ids) pairs, company id 0 for a set of shared records only.
        """
        clusters = []
        for company in {c for c in company_of.values() if c}:
            allowed = (company, 0)
            for members in self._components(
                    e for e in edges if company_of[e[0]] in allowed and company_of[e[1]] in allowed):
                if any(company_of[res_id] == company for res_id in members):
                    clusters.append((company, members))
        for members in self._components(e for e in edges if not company_of[e[0]] and not company_of[e[1]]):
            if not any(members <= other for _company, other in clusters):
                clusters.append((0, members))
        return clusters

    @staticmethod
    def _components(edges: Iterable[tuple]) -> list:
        """Return the connected groups of ids of the pairs (union-find)."""
        parent = {}

        def find(node: int) -> int:
            parent.setdefault(node, node)
            while parent[node] != node:
                parent[node] = parent[parent[node]]
                node = parent[node]
            return node

        for left, right in edges:
            parent[find(left)] = find(right)
        groups = defaultdict(set)
        for node in list(parent):
            groups[find(node)].add(node)
        return list(groups.values())
