import { animationFrame, expect, test } from "@odoo/hoot";
import { click, queryOne } from "@odoo/hoot-dom";
import { mountWithCleanup } from "@web/../tests/web_test_helpers";
import { FvfSection } from "@form_view_field_organizer/fvf_section/fvf_section";

test("section toggles on click and aria-expanded flips", async () => {
    await mountWithCleanup(FvfSection, {
        props: { name: "a", title: "Accounting", startClosed: false },
    });
    expect(".o_fvf_section_toggle").toHaveAttribute("aria-expanded", "true");
    await click(".o_fvf_section_toggle");
    await animationFrame();
    expect(".o_fvf_section_toggle").toHaveAttribute("aria-expanded", "false");
    expect(queryOne(".o_fvf_section_body")).toHaveClass("d-none");
});

test("section starts folded when start_closed", async () => {
    await mountWithCleanup(FvfSection, {
        props: { name: "b", title: "Notes", startClosed: true },
    });
    expect(".o_fvf_section_toggle").toHaveAttribute("aria-expanded", "false");
});
