import hashlib
import logging
import time

from odoo import Command, fields, models
from odoo.fields import Domain
from odoo.exceptions import UserError

from ..const import DUPLICATE_MAX_BLOCK_KEY_LENGTH, DUPLICATE_MAX_BLOCK_SIZE, JOB_BATCH_SIZE
from ..services import matching

_logger = logging.getLogger(__name__)


class DataQualityDuplicateCheck(models.Model):
    """One background run of a duplicate rule, in three phases with a checkpoint each.

    keys: the records are paged by id and each gets its blocking keys; pairs: the records that
    share a key are scored; groups: the pairs at or above the threshold, minus the pairs set
    aside, are joined into groups. A rule has at most one check queued or running.

    Requirements: FR-DGQ-0039, NFR-DGQ-0003
    """
    _name = 'data.quality.duplicate.check'
    _inherit = ['data.quality.job.mixin']
    _description = 'Data Quality Duplicate Check'
    _order = 'id desc'
    _dgq_cron_xmlid = 'data_governance_quality_center_app.ir_cron_dgq_duplicate_check'

    rule_id = fields.Many2one(
        'data.quality.duplicate.rule', string='Rule', required=True, ondelete='cascade', index=True)
    phase = fields.Selection([
        ('keys', 'Finding candidates'),
        ('pairs', 'Scoring pairs'),
        ('groups', 'Building groups'),
    ], required=True, default='keys', copy=False)
    last_id = fields.Integer(copy=False, help="The last record whose keys were written.")
    last_key = fields.Char(copy=False, help="The last blocking key whose records were scored.")
    pair_data = fields.Json(copy=False, help="The pairs scored at or above the threshold: id, id, score.")
    pairs_scored = fields.Integer(copy=False)
    blocks_skipped = fields.Integer(
        copy=False, help="Blocks of records with the same key that were too large to compare.")
    groups_found = fields.Integer(copy=False)

    def action_start(self):
        """Queue the check from its first phase, unless a check of the rule is already waiting.

        Requirements: FR-DGQ-0039
        """
        self._check_not_overlapping()
        self._reset_progress()
        return super().action_start()

    def action_resume(self):
        """Queue a failed check again, unless a check of the rule is already waiting."""
        self._check_not_overlapping()
        return super().action_resume()

    def _check_not_overlapping(self):
        # sudo: lock the rule row so that two quick starts of the same rule cannot both pass the
        # count below; the lock lets the check rows keep pointing at the rule
        self.rule_id.sudo().lock_for_update(allow_referencing=True)
        for check in self:
            # sudo: a check of the rule that another user queued still counts
            other = self.sudo().search_count([
                ('rule_id', '=', check.rule_id.id), ('id', '!=', check.id),
                ('state', 'in', ('queued', 'running'))], limit=1)
            if other:
                raise UserError(self.env._("A check of this rule is already queued or running."))

    def _reset_progress(self):
        # sudo: the work table has no access group
        self.env['data.quality.duplicate.key'].sudo().search([('check_id', 'in', self.ids)]).unlink()
        self.write({
            'phase': 'keys', 'last_id': 0, 'last_key': False, 'pair_data': False,
            'pairs_scored': 0, 'blocks_skipped': 0, 'groups_found': 0,
            'done_count': 0, 'total_count': 0,
        })

    # ---- batches -----------------------------------------------------------

    def _dgq_run_batch(self):
        """Process one batch of the current phase and return ``(processed, remaining)``.

        Requirements: FR-DGQ-0039, NFR-DGQ-0003
        """
        self.ensure_one()
        phase, started = self.phase, time.monotonic()
        if phase == 'keys':
            processed, remaining = self._run_keys_batch()
        elif phase == 'pairs':
            processed, remaining = self._run_pairs_batch()
        else:
            processed, remaining = self._run_groups_batch()
        _logger.info(
            "dgq duplicate check %s: rule %s, phase %s, %s processed, %s remaining, %.2fs",
            self.id, self.rule_id.id, phase, processed, remaining, time.monotonic() - started)
        return processed, remaining

    def _record_domain(self):
        """Domain of the active records the rule compares: its filter and its company."""
        rule = self.rule_id
        domain = rule._get_domain_filter()
        if rule.company_id and 'company_id' in self.env[rule.model_name]._fields:
            domain &= Domain('company_id', '=', rule.company_id.id)
        return domain

    def _read_values(self, ids):
        """Return ``{id: values}`` of the matching fields and the company of the records that
        still match the check: active, in the rule's filter and company."""
        rule = self.rule_id
        # sudo: a check reads the records of every company the rule covers, whoever started it.
        # Searching again (not browsing) leaves out the records deleted, archived or moved out
        # of the rule's filter since an earlier phase.
        records = self.env[rule.model_name].sudo().search(self._record_domain() & Domain('id', 'in', ids))
        names = [name for name, _method, _weight in rule._get_match_lines()]
        has_company = 'company_id' in records._fields
        read_names = names + (['company_id'] if has_company else [])
        values = {}
        for data in records.read(read_names, load=''):
            row = {name: data[name] for name in names}
            row['__company'] = data['company_id'] if has_company else False
            values[data['id']] = row
        return values

    @staticmethod
    def _stored_block_key(company, key):
        """Return the key stored for a record: its company and key, hashed when too long to index."""
        if len(key) > DUPLICATE_MAX_BLOCK_KEY_LENGTH:
            key = hashlib.sha256(key.encode()).hexdigest()
        return '%s|%s' % (company, key)

    def _run_keys_batch(self):
        rule = self.rule_id
        lines = rule._get_match_lines()
        # sudo: see _read_values
        Model = self.env[rule.model_name].sudo()
        domain = self._record_domain() & Domain('id', '>', self.last_id)
        ids = Model.search(domain, order='id', limit=JOB_BATCH_SIZE).ids
        if not ids:
            self.phase = 'pairs'
            return 0, 1
        key_values = []
        for res_id, values in self._read_values(ids).items():
            company = values['__company'] or 0
            key_values.extend(
                {'check_id': self.id, 'block_key': self._stored_block_key(company, key), 'res_id': res_id}
                for key in matching.block_keys(lines, values))
        # sudo: the work table has no access group
        self.env['data.quality.duplicate.key'].sudo().create(key_values)
        self.last_id = ids[-1]
        remaining = Model.search_count(domain & Domain('id', '>', ids[-1]))
        return len(ids), remaining or 1

    def _run_pairs_batch(self):
        rule = self.rule_id
        lines = rule._get_match_lines()
        # sudo: the work table has no access group
        Key = self.env['data.quality.duplicate.key'].sudo()
        blocks = Key._read_group(
            [('check_id', '=', self.id), ('block_key', '>', self.last_key or '')],
            ['block_key'], ['__count'], order='block_key', limit=JOB_BATCH_SIZE)
        known = {(a, b) for a, b, _score in self.pair_data or []}
        found = list(self.pair_data or [])
        processed = skipped = scored = 0
        last_key = self.last_key
        consumed_all = True
        compared = []
        for block_key, count in blocks:
            if processed >= JOB_BATCH_SIZE:
                consumed_all = False
                break
            last_key = block_key
            processed += count
            if count > DUPLICATE_MAX_BLOCK_SIZE:
                skipped += 1
            elif count >= 2:
                compared.append(block_key)
        members = {}
        for key in Key.search_fetch(
                [('check_id', '=', self.id), ('block_key', 'in', compared)], ['block_key', 'res_id']):
            members.setdefault(key.block_key, []).append(key.res_id)
        values = self._read_values(sorted({res_id for ids in members.values() for res_id in ids}))
        for ids in members.values():
            ids = sorted(res_id for res_id in ids if res_id in values)
            for position, first in enumerate(ids):
                for second in ids[position + 1:]:
                    if (first, second) in known:
                        continue
                    scored += 1
                    score = matching.pair_score(lines, values[first], values[second])
                    if score is not None and score >= rule.threshold:
                        known.add((first, second))
                        found.append([first, second, score])
        self.write({
            'last_key': last_key, 'pair_data': found,
            'pairs_scored': self.pairs_scored + scored, 'blocks_skipped': self.blocks_skipped + skipped,
        })
        if consumed_all and len(blocks) < JOB_BATCH_SIZE:
            self.phase = 'groups'
        return processed, 1

    def _run_groups_batch(self):
        rule = self.rule_id
        lines = rule._get_match_lines()
        # sudo: the work table and the dismissals are the app's own data
        dismissed = {
            (d.res_id_a, d.res_id_b)
            for d in self.env['data.quality.duplicate.dismissal'].sudo().search(  # sudo: see above
                [('rule_id', '=', rule.id)])
        }
        candidates = [(a, b, score) for a, b, score in self.pair_data or [] if (a, b) not in dismissed]
        values = self._read_values(sorted({res_id for a, b, _score in candidates for res_id in (a, b)}))
        # a record deleted, archived or filtered out since the pairs phase has no values, and a
        # record moved to another company no longer pairs with its old company: such pairs drop out
        pairs = [
            (a, b, score) for a, b, score in candidates
            if a in values and b in values and values[a]['__company'] == values[b]['__company']
        ]
        components = matching.group_pairs(pairs)
        # sudo: the check writes the groups as the app, whoever started it
        Group = self.env['data.quality.duplicate.group'].sudo()
        existing = {
            frozenset(group.line_ids.mapped('res_id')): group
            for group in Group.search([('rule_id', '=', rule.id), ('state', '=', 'open')])
        }
        # sudo: see _read_values
        records = self.env[rule.model_name].sudo().browse(sorted(values))
        names = {record.id: record.display_name for record in records}
        for ids, best in components:
            first = ids[0]
            line_values = [Command.create({
                'sequence': position, 'res_id': res_id, 'record_name': names.get(res_id),
                'score': 100.0 if res_id == first
                else matching.pair_score(lines, values[first], values[res_id]) or 0.0,
            }) for position, res_id in enumerate(ids)]
            group_values = {
                'name': names.get(first), 'score': best, 'check_id': self.id,
                'res_model': rule.model_name, 'company_id': values[first]['__company'],
                'line_ids': line_values,
            }
            kept = existing.pop(frozenset(ids), None)
            if kept:
                kept.write(dict(group_values, line_ids=[Command.clear()] + line_values))
            else:
                group_values['rule_id'] = rule.id
                Group.create(group_values)
        Group.browse([group.id for group in existing.values()]).unlink()
        self.groups_found = len(components)
        # sudo: the work table has no access group
        self.env['data.quality.duplicate.key'].sudo().search([('check_id', '=', self.id)]).unlink()
        return len(pairs), 0
