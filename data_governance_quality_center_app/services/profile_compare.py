"""Comparing two profiles and building the profile exports (XLSX sections and PDF figures)."""
import io

import xlsxwriter

from odoo import fields

from .profiling import NUMBER_TYPES



def _shape_names(line):
    return {shape for shape, _count in line.shapes or []}


def _stat_changes(env, old, new):
    """One text of the number statistics that differ, e.g. ``Maximum 9 -> 12``; empty when none."""
    if new.field_type not in NUMBER_TYPES:
        return ''
    labels = {
        'min_value': env._("Minimum"), 'max_value': env._("Maximum"),
        'mean_value': env._("Average"), 'median_value': env._("Median")}
    changes = [
        env._("%(stat)s %(old)s -> %(new)s", stat=label, old=round(old[name], 4), new=round(new[name], 4))
        for name, label in labels.items() if old[name] != new[name]]
    if old.outlier_count != new.outlier_count:
        changes.append(env._("Unusual values %(old)s -> %(new)s", old=old.outlier_count, new=new.outlier_count))
    return '; '.join(changes)


def _shape_changes(env, old, new):
    """The shapes that appeared and disappeared between two lines, as one text."""
    gone, came = sorted(_shape_names(old) - _shape_names(new)), sorted(_shape_names(new) - _shape_names(old))
    parts = []
    if came:
        parts.append(env._("New: %s", ', '.join(came)))
    if gone:
        parts.append(env._("Gone: %s", ', '.join(gone)))
    return '; '.join(parts)


def compare_profiles(env, profile, earlier):
    """Compare ``profile`` with the ``earlier`` profile of the same model and company, per field.

    Returns one dict per field, ordered by field name: its ``field_id``, ``field_name``, a ``status``
    (``changed``, ``same``, ``added`` for a field only in ``profile``, ``dropped`` for a field only
    in ``earlier``) and, for a field in both, the empty share and distinct values of each profile
    with their change (later minus earlier) and the texts of the changed shapes and number statistics.

    Requirements: FR-DGQ-0056
    """
    # sudo: the lines are read as the app; the caller was allowed to read both profiles
    new_lines = {line.field_name: line for line in profile.sudo().line_ids}
    old_lines = {line.field_name: line for line in earlier.sudo().line_ids}  # sudo: as above
    rows = []
    for name in sorted(set(new_lines) | set(old_lines)):
        new, old = new_lines.get(name), old_lines.get(name)
        row = {'field_id': (new or old).field_id.id, 'field_name': name}
        if not (new and old):
            row['status'] = 'added' if new else 'dropped'
            rows.append(row)
            continue
        row.update(
            empty_share_old=old.empty_share, empty_share_new=new.empty_share,
            empty_share_change=new.empty_share - old.empty_share,
            distinct_old=old.distinct_count, distinct_new=new.distinct_count,
            distinct_change=new.distinct_count - old.distinct_count,
            shapes_change=_shape_changes(env, old, new), numbers_change=_stat_changes(env, old, new))
        changed = (row['empty_share_change'] or row['distinct_change'] or row['shapes_change']
                   or row['numbers_change'])
        row['status'] = 'changed' if changed else 'same'
        rows.append(row)
    return rows


def profile_header(profile):
    """The facts every export names: model, company, date of the profile, its user and the exporter."""
    env = profile.env
    stamp = profile.create_date or fields.Datetime.now()
    return [
        (env._("Model"), profile.model_id.sudo().name or ''),  # sudo: the model's name, not its data
        (env._("Company"), profile.company_id.name or ''),
        (env._("Date"), fields.Datetime.context_timestamp(profile, stamp).strftime('%Y-%m-%d')),
        (env._("User"), profile.user_id.name or ''),
        (env._("Exported by"), env.user.name or ''),
    ]


def _usable_lines(profile):
    """The lines of the profile whose field is still neither excluded nor sensitive."""
    usable = profile.env['data.quality.field.config']._get_usable_field_names(profile.res_model)
    # sudo: the lines are read as the app; the caller was allowed to read the profile
    return profile.sudo().line_ids.filtered(lambda line: line.field_name in usable)


def profile_sections(profile):
    """The figures of a profile as sections ``(title, header, rows)``, one per kind of statistic.

    Fields excluded or marked sensitive after the profile was made are left out, as on the profile.

    Requirements: FR-DGQ-0057
    """
    env = profile.env
    lines = _usable_lines(profile)

    def label(line):
        return line.field_id.field_description or line.field_name

    def pairs(name):
        return [[label(line), str(key), count] for line in lines for key, count in line[name] or []]

    numeric = lines.filtered(lambda line: line.field_type in NUMBER_TYPES)
    links = lines.filtered(lambda line: line.field_type == 'many2one')
    return [
        (env._("Completeness"), [env._("Field"), env._("Type"), env._("Empty"), env._("Empty (%)"),
                                 env._("Distinct Values")],
         [[label(line), line.field_type or '', line.empty_count, round(line.empty_share, 2),
           line.distinct_count] for line in lines]),
        (env._("Frequencies"), [env._("Field"), env._("Value"), env._("Count")], pairs('top_values')),
        (env._("Formats"), [env._("Field"), env._("Shape"), env._("Count")], pairs('shapes')),
        (env._("Numbers"), [env._("Field"), env._("Minimum"), env._("Maximum"), env._("Average"),
                            env._("Median"), env._("Unusual Values")],
         [[label(line), line.min_value, line.max_value, line.mean_value, line.median_value,
           line.outlier_count] for line in numeric]),
        (env._("Links"), [env._("Field"), env._("Linked Record"), env._("Count")],
         [[label(line), str(key), count] for line in links for key, count in line.link_distribution or []]),
        (env._("Unused Choices"), [env._("Field"), env._("Choice")],
         [[label(line), str(choice)] for line in lines for choice in line.unused_choices or []]),
    ]


def profile_xlsx(profile):
    """Return the XLSX bytes of a profile: one sheet per kind of statistic, each naming its source.

    Requirements: FR-DGQ-0057
    """
    output = io.BytesIO()
    workbook = xlsxwriter.Workbook(
        output, {'in_memory': True, 'strings_to_formulas': False, 'strings_to_urls': False})
    header = profile_header(profile)
    for title, columns, rows in profile_sections(profile):
        sheet = workbook.add_worksheet(title[:31])
        for index, (name, value) in enumerate(header):
            sheet.write_string(index, 0, name)
            sheet.write_string(index, 1, value)
        top = len(header) + 1
        for col, name in enumerate(columns):
            sheet.write_string(top, col, name)
        for row, values in enumerate(rows[:sheet.xls_rowmax - top - 1], start=top + 1):
            for col, value in enumerate(values):
                if isinstance(value, str):
                    sheet.write_string(row, col, value[:sheet.xls_strmax])
                else:
                    sheet.write_number(row, col, value)
    workbook.close()
    return output.getvalue()
