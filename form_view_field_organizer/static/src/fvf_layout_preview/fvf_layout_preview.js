import { Component, onWillStart, proxy, useProps } from "@odoo/owl";
import { loadBundle } from "@web/core/assets";
import { cookie } from "@web/core/browser/cookie";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { parseXML, serializeXML } from "@web/core/utils/xml";
import { SearchModel } from "@web/search/search_model";
import { View } from "@web/views/view";
import { standardActionServiceProps } from "@web/webclient/actions/action_plugin";

const viewRegistry = registry.category("views");

/**
 * The search model of a preview: the companies given in the view's context win over the
 * administrator's active companies, which the native search model puts last.
 */
function chosenCompanySearchModel(BaseSearchModel) {
    return class extends BaseSearchModel {
        _getContext() {
            return { ...super._getContext(), allowed_company_ids: this.globalContext.allowed_company_ids };
        }
    };
}

/**
 * Point the arch at a copy of its own view class that reads in the chosen company,
 * so the form keeps its controller and renderer.
 */
async function inChosenCompany(arch) {
    const form = parseXML(arch);
    const viewClass = form.getAttribute("js_class") || "form";
    const previewClass = `fvf_preview_${viewClass}`;
    if (!viewRegistry.contains(viewClass)) {
        // Same lazy load as the native View for a view class not loaded yet.
        await loadBundle(
            cookie.get("color_scheme") === "dark" ? "web.assets_backend_lazy_dark" : "web.assets_backend_lazy"
        );
    }
    if (!viewRegistry.contains(previewClass)) {
        const descr = viewRegistry.get(viewClass);
        viewRegistry.add(previewClass, {
            ...descr,
            SearchModel: chosenCompanySearchModel(descr.SearchModel || SearchModel),
        });
    }
    form.setAttribute("js_class", previewClass);
    return serializeXML(form);
}

/**
 * Client action: the draft layout's form for a chosen group, company and record, read-only.
 * Requirements: FR-FVF-0010, FR-FVF-0011
 */
export class FvfLayoutPreview extends Component {
    static template = "form_view_field_organizer.FvfLayoutPreview";
    static components = { View };
    props = useProps({ ...standardActionServiceProps });

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.state = proxy({ preview: null });
        onWillStart(async () => {
            const previewId = this.props.action.params.preview_id;
            const preview = await this.orm.call("fvf.layout.preview", "get_preview_view", [[previewId]]);
            this.state.preview = { ...preview, arch: await inChosenCompany(preview.arch) };
        });
    }

    get viewProps() {
        const { arch, fields, relatedModels, res_model, res_id, company_id } = this.state.preview;
        return {
            type: "form",
            resModel: res_model,
            resId: res_id,
            arch,
            fields,
            relatedModels,
            context: { allowed_company_ids: [company_id] },
            display: { controlPanel: false },
        };
    }

    /** Back to the layout form, which stays a draft. */
    backToEditor() {
        return this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "fvf.layout",
            res_id: this.state.preview.layout_id,
            views: [[false, "form"]],
        });
    }

    async publish() {
        await this.orm.call("fvf.layout", "action_publish", [[this.state.preview.layout_id]]);
        return this.backToEditor();
    }
}

registry.category("actions").add("fvf_layout_preview", FvfLayoutPreview);
