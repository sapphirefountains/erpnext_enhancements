frappe.pages['hierarchical-task-view'].on_page_load = function(wrapper) {
    const page = frappe.ui.make_app_page({
        parent: wrapper,
        title: 'Hierarchical Task View',
        single_column: true
    });

    // Add a container for our tree
    let tree_container = $('<div class="task-tree-container" style="margin-top: 20px;"></div>').appendTo(page.body);
    tree_container.html('<p class="text-muted">Please select a project to view its tasks.</p>');
    
    let tree = null;

    let project_field = page.add_field({
        label: 'Project',
        fieldname: 'project',
        fieldtype: 'Link',
        options: 'Project',
        change: () => {
            let project = project_field.get_value();
            if (project) {
                // Clear the container before creating a new tree
                tree_container.empty();
                
                // Ensure the "Task" doctype metadata is loaded before creating the tree
                frappe.model.with_doctype("Task", () => {
                    tree = new frappe.views.TreeView({
                        parent: tree_container,
                        doctype: "Task",
                        get_nodes: function() {
                            return frappe.call({
                                method: 'erpnext_enhancements.task_enhancements.page.hierarchical_task_view.hierarchical_task_view.get_project_tasks_hierarchy',
                                args: { project: project }
                            });
                        },
                        // This function is called when a tree node is clicked
                        on_render: (node) => {
                            // Add a link to the actual task document
                            $(node.label_area).find('a').attr('href', `/app/task/${node.data.value}`);
                        },
                        // Disable default add/search functionality as it won't work here
                        add_tree_node: false,
                        search: false
                    });
                    tree.refresh();
                });

            } else {
                tree_container.html('<p class="text-muted">Please select a project to view its tasks.</p>');
            }
        }
    });
}
