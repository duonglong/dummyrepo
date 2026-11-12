"""Layout engine: turns a form arch and a plain layout spec into the organized arch.

Pure functions on lxml trees, no ORM access.
"""
import logging

from lxml import etree

from . import expressions

_logger = logging.getLogger(__name__)

SECTION_CLASS = 'o_fvf_section'
SECTION_XPATH = './/div[@class="%s"]' % SECTION_CLASS


def _occurrences(tree, name):
    """Fields and labels of ``name`` outside any x2many sub-view."""
    fields = tree.xpath('.//field[@name=$n][not(ancestor::field)]', n=name)
    labels = tree.xpath('.//label[@for=$n][not(ancestor::field)]', n=name)
    return fields, labels


def _hide(nodes, hide_refs):
    """Append ``!<group>`` for each group to the groups of every node."""
    if not hide_refs:
        return
    marks = ['!%s' % ref for ref in hide_refs]
    for node in nodes:
        node.set('groups', ','.join(filter(None, [node.get('groups')] + marks)))


def _usable_condition(condition, model_fields, layout_id):
    """Condition lines whose field still exists."""
    usable = [line for line in condition if line['field'] in model_fields]
    for line in condition:
        if line['field'] not in model_fields:
            _logger.debug("fvf: layout %s skipped missing field %s", layout_id, line['field'])
    return usable


def _apply_condition(nodes, condition, model_fields, layout_id):
    usable = _usable_condition(condition, model_fields, layout_id)
    if not usable:
        return
    field_types = {name: info['type'] for name, info in model_fields.items()}
    expression = expressions.build_condition(usable, field_types)
    for node in nodes:
        node.set('invisible', expressions.combine(node.get('invisible'), expression))


def _join_invisible(rules):
    """OR the non-empty ``rules`` together."""
    rules = [rule for rule in rules if rule]
    if len(rules) > 1:
        return ' or '.join('(%s)' % rule for rule in rules)
    return rules[0] if rules else None


def _all_invisible(rules):
    """AND the ``rules``; no rule at all when one occurrence is never invisible."""
    if not all(rules):
        return None
    distinct = list(dict.fromkeys(rules))
    if len(distinct) == 1:
        return distinct[0]
    return ' and '.join('(%s)' % rule for rule in distinct)


def _keep_tail(node):
    """Hand the text following ``node`` to the node before it, or to its parent."""
    if not (node.tail and node.tail.strip()):
        return
    previous = node.getprevious()
    if previous is not None:
        previous.tail = (previous.tail or '') + node.tail
    else:
        parent = node.getparent()
        parent.text = (parent.text or '') + node.tail
    node.tail = None


def _drop(node):
    """Remove ``node`` and every wrapper div it leaves without an element child."""
    _keep_tail(node)
    parent = node.getparent()
    parent.remove(node)
    while parent.tag == 'div' and len(parent) == 0 and parent.getparent() is not None:
        node, parent = parent, parent.getparent()
        _keep_tail(node)
        parent.remove(node)


def _former_containers(field):
    """Containers of ``field``, outermost first, that restrict who sees it."""
    return [node for node in reversed(list(field.iterancestors()))
            if node.get('groups') or node.get('invisible')]


def _group_chain(field, keep_own_groups):
    """Groups a user needs to see ``field`` where it was, outermost first."""
    chain = [node.get('groups') for node in _former_containers(field) if node.get('groups')]
    if keep_own_groups and field.get('groups'):
        chain.append(field.get('groups'))
    return chain


def _shared_groups(chains):
    """Wrapper groups showing the field to every group that saw at least one occurrence.

    The common leading groups stay nested; below them the groups of the
    occurrences are joined as alternatives. When occurrences differ by more
    than one group each, every distinct group is required, which only narrows.
    """
    shared = []
    for step in zip(*chains):
        if len(set(step)) > 1:
            break
        shared.append(step[0])
    rests = [chain[len(shared):] for chain in chains]
    if all(rests) and all(len(rest) == 1 for rest in rests):
        return shared + [','.join(dict.fromkeys(rest[0] for rest in rests))]
    if all(rests):
        return shared + list(dict.fromkeys(group for rest in rests for group in rest))
    return shared


def _visibility_rule(field):
    """Invisible rule under which ``field`` was hidden where it was."""
    return _join_invisible([field.get('invisible')] + [node.get('invisible') for node in _former_containers(field)])


def _set_invisible(node, rule):
    if rule:
        node.set('invisible', rule)
    else:
        node.attrib.pop('invisible', None)


def _required_groups(chains):
    """Every distinct group of the ``chains``, nested: shown only to users who saw every appearance."""
    return list(dict.fromkeys(group for chain in chains for group in chain))


def _merged_visibility(fields):
    """Invisible rule and wrapper groups under which the merged field is shown.

    Appearances seen by the same groups: shown whenever any of them showed it
    (AND of their rules). Appearances sharing one rule: shown to every group
    that saw one of them. Appearances differing in both: shown only where
    every appearance would show it (OR of the rules, every group required),
    which never reveals (BR-FVF-0001).
    """
    several = len(fields) > 1
    rules = [_visibility_rule(field) for field in fields]
    chains = [_group_chain(field, several) for field in fields]
    if len({tuple(chain) for chain in chains}) == 1:
        return _all_invisible(rules), chains[0]
    if len(set(rules)) == 1:
        return rules[0], _shared_groups(chains)
    return _join_invisible(rules), _required_groups(chains)


def _take_out(fields, labels):
    """Detach the occurrences of a field and its labels, keep one.

    Return ``(top, moved)``: ``top`` the nodes to place, ``moved`` the kept
    field and its labels. The first occurrence is kept with its labels,
    wrapped in one div per groups value (outermost first), with the
    visibility of :func:`_merged_visibility`.
    """
    first = fields[0]
    invisible, groups = _merged_visibility(fields)
    moved = [label for label in labels if label.getparent() is first.getparent()] + [first]
    for node in fields + labels:
        _drop(node)
    if len(fields) > 1:
        first.attrib.pop('groups', None)
    _set_invisible(first, invisible)
    for label in moved[:-1]:
        _set_invisible(label, _join_invisible([label.get('invisible'), invisible]))
    wrappers = [etree.Element('div', groups=value) for value in groups]
    for outer, inner in zip(wrappers, wrappers[1:]):
        outer.append(inner)
    if not wrappers:
        return moved, moved
    wrappers[-1].extend(moved)
    return wrappers[:1], moved


def _section_node(section):
    node = etree.Element(
        'div', {'class': SECTION_CLASS, 'name': 'fvf_section_%s' % section['id'],
                'string': section['name'], 'start_closed': '1' if section['start_closed'] else '0'})
    etree.SubElement(node, 'group')
    return node


def _attach_sections(tree, section_nodes):
    """Append the sections as last children of the sheet, else of the form before the chatter."""
    sheets = tree.xpath('.//sheet[not(ancestor::field)]')
    sheet = sheets[0] if sheets else None
    if sheet is not None:
        sheet.extend(section_nodes)
        return
    chatter = tree.find('chatter')
    for node in section_nodes:
        if chatter is not None:
            chatter.addprevious(node)
        else:
            tree.append(node)


def apply_layout(arch_tree, spec, model_fields):
    """Apply the layout ``spec`` to ``arch_tree`` and return it.

    Lines naming a field missing from the model or the arch are skipped.

    Requirements: FR-FVF-0002, FR-FVF-0004, FR-FVF-0005, FR-FVF-0006, FR-FVF-0007, FR-FVF-0015,
    BR-FVF-0001, BR-FVF-0005, BR-FVF-0006
    """
    section_nodes = {section['id']: _section_node(section) for section in spec['sections']}
    placed = {section_id: [] for section_id in section_nodes}
    for position, line in enumerate(spec['lines']):
        fields, labels = _occurrences(arch_tree, line['field'])
        if line['field'] not in model_fields or not fields:
            _logger.debug("fvf: layout %s skipped missing field %s", spec['layout_id'], line['field'])
            continue
        nodes = fields + labels
        if line['section_id'] in section_nodes:
            top, nodes = _take_out(fields, labels)
            placed[line['section_id']].append((line['sequence'], position, top))
        _hide(nodes, line['hide_refs'])
        _apply_condition(nodes, line['condition'], model_fields, spec['layout_id'])
    for section_id, items in placed.items():
        group = section_nodes[section_id][0]
        for _sequence, _position, nodes in sorted(items, key=lambda item: item[:2]):
            group.extend(nodes)
    _attach_sections(arch_tree, list(section_nodes.values()))
    return arch_tree


def prune_empty_sections(tree):
    """Drop sections without a field; hide a section whose fields are all invisible.

    Requirements: FR-FVF-0004, FR-FVF-0015
    """
    for section in tree.xpath(SECTION_XPATH):
        fields = section.xpath('.//field[not(ancestor::field)]')
        if not fields:
            section.getparent().remove(section)
        elif all(field.get('invisible') for field in fields):
            rules = dict.fromkeys(field.get('invisible') for field in fields)
            section.set('invisible', ' and '.join('(%s)' % rule for rule in rules))
    return tree
