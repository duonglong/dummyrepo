import logging
from collections.abc import Iterable

from odoo import api, models

_logger = logging.getLogger(__name__)


class DuplicateCheckMixin(models.AbstractModel):
    _name = 'duplicate.check.mixin'
    _description = 'Duplicate Check on Save'

    @api.model_create_multi
    def create(self, vals_list: list[dict]) -> models.BaseModel:
        """Requirements: FR-DRD-0003, FR-DRD-0004 (check every new record after the native save)."""
        records = super().create(vals_list)
        records._duplicate_check(None)
        return records

    def write(self, vals: dict) -> bool:
        """Requirements: FR-DRD-0003, FR-DRD-0004 (check after a save that changed a field of a rule)."""
        result = super().write(vals)
        self._duplicate_check(vals.keys())
        return result

    def unlink(self) -> bool:
        """Requirements: FR-DRD-0004 (a deleted record leaves no line in any set)."""
        # sudo: a plain user cannot read the sets, yet their lines must not point to a deleted record
        self.env['duplicate.group.line'].sudo().search([
            ('res_model', '=', self._name), ('res_id', 'in', self.ids)]).unlink()
        return super().unlink()

    def _duplicate_check(self, changed_fields: Iterable[str] | None) -> None:
        """Requirements: FR-DRD-0003, FR-DRD-0004, NFR-DRD-0001.

        A write that touches no field of a rule runs no matching, so unrelated saves stay fast.

        Record the matches of the saved records in open sets and warn the saving user. It never
        raises: a failing check is logged and the save goes on.
        """
        # sudo: the saver need not see rules or sets; only this check reads and writes them
        rules = self.env['duplicate.rule'].sudo().search([('model_name', '=', self._name)])
        if changed_fields is not None:
            rules = rules.filtered(
                lambda r: set(r.condition_ids.field_id.mapped('name')) & set(changed_fields))
        if not rules:
            return
        try:
            with self.env.cr.savepoint():
                found = self._duplicate_record_matches(rules)
                if found and self._duplicate_can_warn():
                    self._duplicate_warn(found)
        except Exception:  # noqa: BLE001 - the check must never block a save
            _logger.exception("Duplicate check of %s %s failed", self._name, self.ids)

    def _duplicate_record_matches(self, rules: models.BaseModel) -> list:
        """Record the matches of every active saved record; return (rule, matches) pairs."""
        # sudo: the matcher must see every record of the type and the sets, whatever the saver may read
        matcher = self.env['duplicate.matcher'].sudo()
        group = self.env['duplicate.group'].sudo()
        found = []
        for record in self.sudo().filtered('active'):  # sudo: same reason as above
            for rule in rules:
                matches = group._record_matches(rule, record, matcher.find_matches(record, rule))
                if matches:
                    found.append((rule, matches))
        return found

    def _duplicate_can_warn(self) -> bool:
        """Tell whether the save came from a screen: imports and installs have nobody to warn."""
        return not (self.env.context.get('import_file') or self.env.context.get('install_mode'))

    def _duplicate_warn(self, found: list) -> None:
        """Requirements: FR-DRD-0003 (show the saving user the matches and the rule)."""
        # The matches were found with sudo: name only those the saving user may read (company rules)
        readable = [(rule, matches.with_env(self.env)._filtered_access('read')) for rule, matches in found]
        lines = [
            self.env._("%(names)s (rule: %(rule)s)",
                       names=', '.join(matches.mapped('display_name')), rule=rule.name)
            for rule, matches in readable if matches]
        if not lines:
            return
        self.env.user._bus_send('simple_notification', {
            'title': self.env._("Possible duplicate"),
            'message': self.env._("This record looks like: %s", '; '.join(lines)),
            'type': 'warning',
        })
