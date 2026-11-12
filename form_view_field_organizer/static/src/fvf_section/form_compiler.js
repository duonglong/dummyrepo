import { registry } from "@web/core/registry";
import { patch } from "@web/core/utils/patch";
import { append, createElement } from "@web/core/utils/xml";
import { toStringExpression } from "@web/views/utils";
import { FormRenderer } from "@web/views/form/form_renderer";
import { FvfSection } from "./fvf_section";

/**
 * Compile the section block the layout engine adds (div.o_fvf_section) into the FvfSection component.
 * Requirements: FR-FVF-0003, FR-FVF-0004
 *
 * @this {import("@web/views/form/form_compiler").FormCompiler}
 */
function compileFvfSection(node, params) {
    const section = createElement("t", {
        "t-component": "__comp__.fvfComponents.FvfSection",
        name: toStringExpression(node.getAttribute("name") || ""),
        title: toStringExpression(node.getAttribute("string") || ""),
        startClosed: node.getAttribute("start_closed") === "1" ? "true" : "false",
    });
    const body = createElement("t", { "t-set-slot": "default" });
    append(section, body);
    for (const child of node.children) {
        const compiled = this.compileNode(child, { ...params, currentSlot: body });
        if (compiled) {
            append(body, compiled);
        }
    }
    return section;
}

registry.category("form_compilers").add("fvf_section_compiler", {
    selector: "div.o_fvf_section",
    fn: compileFvfSection,
    doNotCopyAttributes: true,
});

patch(FormRenderer.prototype, {
    setup() {
        super.setup();
        this.fvfComponents = { FvfSection };
    },
});
