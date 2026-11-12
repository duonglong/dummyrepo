import { expect, test } from "@odoo/hoot";
import { FvfLayoutLines } from "@form_view_field_organizer/fvf_layout_lines/fvf_layout_lines";

test("blocks start with the original place and list rows in sequence", () => {
    const line = (resId, sequence, sectionId, missing = false) => ({
        resId,
        data: { sequence, section_id: sectionId ? { id: sectionId } : false, missing },
    });
    const fake = {
        list: { records: [line(1, 2, 7), line(2, 1, 7), line(3, 1, false, true)] },
        props: { record: { data: { section_ids: { records: [{ resId: 7, data: { name: "A", sequence: 1 } }] } } } },
    };
    const blocks = Object.getOwnPropertyDescriptor(FvfLayoutLines.prototype, "blocks").get.call(fake);
    expect(blocks.map((b) => b.sectionId)).toEqual([false, 7]);
    expect(blocks[1].lines.map((l) => l.resId)).toEqual([2, 1]);
    expect(blocks[0].lines[0].data.missing).toBe(true);
});
