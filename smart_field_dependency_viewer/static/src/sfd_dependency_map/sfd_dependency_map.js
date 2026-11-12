import { Component, markup, proxy, useProps } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { user } from "@web/core/user";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import { exportData, exportImage } from "./sfd_export";

/**
 * Shows the dependents table, the "Depends on" list, the chain diagram and the empty message.
 * Requirements: FR-SFD-0004, FR-SFD-0005, FR-SFD-0006, FR-SFD-0007, FR-SFD-0010, FR-SFD-0011, FR-SFD-0012
 */
export class SfdDependencyMap extends Component {
    static template = "smart_field_dependency_viewer.SfdDependencyMap";
    props = useProps({ ...standardFieldProps });

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        // the map redrawn after a limit change, for the field value it was drawn from
        this.state = proxy({ redrawn: null, drawnFrom: null });
    }

    get map() {
        const value = this.props.record.data[this.props.name] || false;
        return this.state.drawnFrom === value ? this.state.redrawn : value;
    }

    get hasDependents() {
        return Boolean(this.map && !this.map.empty);
    }

    /** The SVG as the server built it (every label is escaped there). */
    get diagram() {
        return markup(this.map.svg);
    }

    get emptyMessage() {
        return _t("No computed or related field depends on this field.");
    }

    /**
     * Download the shown map as a PNG image.
     * Requirements: FR-SFD-0011
     */
    async onExportImage() {
        try {
            await exportImage(this.map);
        } catch {
            this.notification.add(_t("The image could not be created in this browser."), { type: "danger" });
        }
    }

    /**
     * Download the shown map as a JSON data file.
     * Requirements: FR-SFD-0012
     */
    onExportData() {
        exportData(this.map);
    }

    /**
     * Save the person's own depth limit, then redraw the map with it.
     * Requirements: FR-SFD-0010
     */
    async onLimitChange(ev) {
        const input = ev.target;
        const current = this.map;
        try {
            await this.orm.call("res.users", "sfd_set_depth_limit", [[user.userId], input.value]);
        } catch (error) {
            this.notification.add(error.data?.message || error.message, { type: "danger" });
            input.value = current.depth_limit;
            return;
        }
        this.state.drawnFrom = this.props.record.data[this.props.name] || false;
        this.state.redrawn = await this.orm.call("sfd.dependency.viewer", "get_dependency_map", [
            current.start.model,
            current.start.field,
        ]);
    }
}

export const sfdDependencyMapField = {
    component: SfdDependencyMap,
    displayName: _t("Dependency map"),
    supportedTypes: ["json"],
};

registry.category("fields").add("sfd_dependency_map", sfdDependencyMapField);
