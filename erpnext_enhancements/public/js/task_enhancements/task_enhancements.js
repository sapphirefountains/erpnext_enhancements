/**
 * Overrides the default get_tree_nodes method for the TreeView.
 *
 * This function is responsible for fetching the hierarchical task data for a
 * selected project and feeding it to the tree view component. It calls the
 * custom Python method `get_project_tasks_hierarchy`.
 *
 * @param {string} doctype - The doctype for which to fetch nodes (e.g., "Task").
 * @param {string} parent - The parent node from which to fetch children.
 * @param {Function} callback - The callback function to be executed with the fetched nodes.
 */
frappe.views.TreeView.prototype.get_tree_nodes = function(doctype, parent, callback) {
    if (parent === this.doctype) {
        parent = null;
    }

    frappe.call({
        method: 'erpnext_enhancements.task_enhancements.page.hierarchical_task_view.hierarchical_task_view.get_project_tasks_hierarchy',
        args: {
            project: this.page.fields_dict.project.get_value()
        },
        callback: function(r) {
            callback(r.message);
        }
    });
};
