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
    for node in nodes:
        marks = ['!%s' % ref for ref in hide_refs]
        node.set('groups', ','.join(filter(None, [node.get('groups')] + marks)))


def _usable_condition(condition, model_fields):
    """Condition lines whose field still exists."""
    usable = [line for line in condition if line['field'] in model_fields]
    if len(usable) < len(condition):
        _logger.debug("Condition lines on missing fields skipped: %s", condition)
    return usable


def _apply_condition(nodes, condition, model_fields):
    usable = _usable_condition(condition, model_fields)
    if not usable:
        return
    field_types = {name: info['type'] for name, info in model_fields.items()}
    expression = expressions.build_condition(usable, field_types)
    for node in nodes:
        node.set('invisible', expressions.combine(node.get('invisible'), expression))


def _join_invisible(rules):
    rules = [rule for rule in rules if rule]
    if len(rules) > 1:
        return ' or '.join('(%s)' % rule for rule in rules)
    return rules[0] if rules else None


def _drop(node):
    """Remove ``node`` and every wrapper div it leaves without an element child."""
    parent = node.getparent()
    parent.remove(node)
    while parent.tag == 'div' and len(parent) == 0 and parent.getparent() is not None:
        node, parent = parent, parent.getparent()
        parent.remove(node)


def _former_containers(field):
    """Containers of ``field``, outermost first, that restrict who sees it."""
    return [node for node in reversed(list(field.iterancestors()))
            if node.get('groups') or node.get('invisible')]


def _take_out(fields, labels):
    """Detach the first occurrence and its labels, drop the rest.

    Return the node to place: the moved field, with its labels, wrapped in one
    div per former container ``groups`` value (outermost first) and carrying
    the invisible rules of its former containers.
    """
    first = fields[0]
    containers = _former_containers(first)
    moved = [label for label in labels if label.getparent() is first.getparent()] + [first]
    invisible = _join_invisible([first.get('invisible')] + [node.get('invisible') for node in containers])
    for node in fields + labels:
        _drop(node)
    if invisible:
        first.set('invisible', invisible)
    wrappers = [etree.Element('div', groups=node.get('groups')) for node in containers if node.get('groups')]
    for outer, inner in zip(wrappers, wrappers[1:]):
        outer.append(inner)
    for node in moved:
        node.tail = None
    if not wrappers:
        return moved
    wrappers[-1].extend(moved)
    return wrappers[:1]


def _section_node(section):
    node = etree.Element(
        'div', {'class': SECTION_CLASS, 'name': 'fvf_section_%s' % section['id'],
                'string': section['name'], 'start_closed': '1' if section['start_closed'] else '0'})
    etree.SubElement(node, 'group')
    return node


def _attach_sections(tree, section_nodes):
    """Append the sections as last children of the sheet, else of the form before the chatter."""
    sheet = tree.find('.//sheet')
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
            _logger.debug("Layout %s: line on missing field %s skipped", spec['layout_id'], line['field'])
            continue
        _hide(fields + labels, line['hide_refs'])
        _apply_condition(fields + labels, line['condition'], model_fields)
        if line['section_id'] in section_nodes:
            placed[line['section_id']].append((line['sequence'], position, _take_out(fields, labels)))
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
        fields = section.findall('.//field')
        if not fields:
            section.getparent().remove(section)
        elif all(field.get('invisible') for field in fields):
            rules = dict.fromkeys(field.get('invisible') for field in fields)
            section.set('invisible', ' and '.join('(%s)' % rule for rule in rules))
    return tree
