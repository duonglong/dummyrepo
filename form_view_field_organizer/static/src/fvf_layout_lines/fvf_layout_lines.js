import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { useSortable } from "@web/core/utils/sortable_owl";
import { X2ManyField, x2ManyField } from "@web/views/fields/x2many/x2many_field";
import { signal } from "@odoo/owl";

/**
 * Field lines of a layout as one block per section, with an "Original place" block first.
 * Rows drag within a block (writes sequence) and across blocks (writes section_id and sequence).
 *
 * Requirements: FR-FVF-0006, FR-FVF-0007, FR-FVF-0015
 */
export class FvfLayoutLines extends X2ManyField {
    static template = "form_view_field_organizer.FvfLayoutLines";

    setup() {
        super.setup();
        this.blocksRef = signal.ref();
        useSortable({
            ref: this.blocksRef,
            elements: ".o_fvf_line_row",
            groups: ".o_fvf_line_block",
            handle: ".o_fvf_line_handle",
            connectGroups: true,
            cursor: "grabbing",
            enable: () => !this.props.readonly,
            onDrop: (params) => this.onDrop(params),
        });
    }

    /**
     * Blocks in display order: the original place first, then the saved sections in section order.
     * Every block has a key that is never falsy (the original place is "original"), so it can sit in
     * a data attribute. Sections not saved yet get no block (lines cannot point at a virtual id); lines
     * whose section is not a block show in the original place.
     */
    get blocks() {
        const lines = this.list.records;
        const sections = this.props.record.data.section_ids.records
            .filter((section) => section.resId)
            .sort((a, b) => a.data.sequence - b.data.sequence || a.resId - b.resId);
        const sectionIds = new Set(sections.map((section) => section.resId));
        const sectionOf = (line) => {
            const id = line.data.section_id?.id;
            return sectionIds.has(id) ? id : false;
        };
        const blockOf = (sectionId, title) => ({
            key: sectionId || "original",
            sectionId,
            title,
            lines: lines
                .filter((line) => sectionOf(line) === sectionId)
                .sort((a, b) => a.data.sequence - b.data.sequence),
        });
        return [
            blockOf(false, ""),
            ...sections.map((section) => blockOf(section.resId, section.data.name)),
        ];
    }

    groupNames(line) {
        return line.data.hidden_group_ids.records.map((group) => group.data.display_name);
    }

    openLine(line) {
        this.openRecord(line);
    }

    onRowKeydown(ev, line) {
        if (ev.target === ev.currentTarget && (ev.key === "Enter" || ev.key === " ")) {
            ev.preventDefault();
            this.openLine(line);
        }
    }

    _lineById(id) {
        return this.list.records.find((line) => String(line.resId || line._virtualId) === id);
    }

    /** Write section_id for the moved row and sequence for every row of the target block. */
    async onDrop({ element, parent, previous }) {
        const moved = this._lineById(element.dataset.lineId);
        const block = this.blocks.find(
            (b) => b.key === parent.dataset.sectionKey
        );
        if (!moved || !block) {
            return;
        }
        const ordered = block.lines.filter((line) => line !== moved);
        const previousLine = previous && this._lineById(previous.dataset.lineId);
        ordered.splice(previousLine ? ordered.indexOf(previousLine) + 1 : 0, 0, moved);
        const section = block.sectionId
            ? this.props.record.data.section_ids.records.find((s) => s.resId === block.sectionId)
            : null;
        const sectionValue = section
            ? { id: section.resId, display_name: section.data.name }
            : false;
        for (const [index, line] of ordered.entries()) {
            const values = {};
            if (line.data.sequence !== index + 1) {
                values.sequence = index + 1;
            }
            if (line === moved) {
                values.section_id = sectionValue;
            }
            if (Object.keys(values).length) {
                await line.update(values);
            }
        }
    }
}

registry.category("fields").add("fvf_layout_lines", {
    ...x2ManyField,
    component: FvfLayoutLines,
    displayName: _t("Layout field lines"),
});
