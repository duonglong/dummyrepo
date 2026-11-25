import { Component, onWillStart, proxy, useProps } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { formatMonetary } from "@web/views/fields/formatters";
import { standardActionServiceProps } from "@web/webclient/actions/action_plugin";

export class SpmDashboard extends Component {
    static template = "supplier_procurement_management_hub.Dashboard";
    props = useProps({ ...standardActionServiceProps });

    setup() {
        this.orm = useService("orm");
        this.actionService = useService("action");
        this.state = proxy({ tiles: [] });
        onWillStart(async () => {
            this.state.tiles = await this.orm.call("procurement.dashboard", "get_tiles", []);
        });
    }

    figure(tile) {
        if (tile.key === "spend_month") {
            return formatMonetary(tile.amount, { currencyId: tile.currency_id });
        }
        return String(tile.count);
    }

    openTile(tile) {
        return this.actionService.doAction(tile.action);
    }
}

registry.category("actions").add("spm_dashboard", SpmDashboard);
