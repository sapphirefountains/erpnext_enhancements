// Opportunity Kanban column totals, migrated from Client Script
// "Opportunity Amount Totals Based on Status" (Opportunity, List).
// Patches the Kanban view to show the summed opportunity_amount per column.

if (!frappe.views.KanbanView.prototype.hasOwnProperty("is_patched_for_totals")) {
    frappe.views.KanbanView.prototype.original_refresh = frappe.views.KanbanView.prototype.refresh;

    frappe.views.KanbanView.prototype.render_opportunity_totals = function () {
        // Only act on the Opportunity Kanban.
        if (this.doctype !== "Opportunity") return;

        if (!this.columns || !Array.isArray(this.columns)) {
            return;
        }

        this.columns.forEach((column) => {
            const column_total = this.data
                .filter((item) => item[this.field_name] === column.name)
                .reduce((sum, item) => sum + (item.opportunity_amount || 0), 0);

            const header_element = this.wrapper.find(
                `.kanban-column[data-column-value="${column.name}"] .kanban-column-header`
            );

            if (header_element.length === 0) return;

            let total_wrapper = header_element.find(".kanban-total-wrapper");

            if (total_wrapper.length === 0) {
                total_wrapper = $(`<div class="kanban-total-wrapper pull-right"></div>`).appendTo(
                    header_element
                );
            }

            const formatted_total = frappe.format(column_total, {
                doctype: "Opportunity",
                fieldname: "opportunity_amount",
            });
            total_wrapper.html(
                `<span class="indicator-pill grey" style="margin-left: 10px;">${formatted_total}</span>`
            );
        });
    };

    frappe.views.KanbanView.prototype.refresh = function (...args) {
        this.original_refresh(...args);
        setTimeout(() => this.render_opportunity_totals(), 1000);
    };

    frappe.views.KanbanView.prototype.is_patched_for_totals = true;
}
