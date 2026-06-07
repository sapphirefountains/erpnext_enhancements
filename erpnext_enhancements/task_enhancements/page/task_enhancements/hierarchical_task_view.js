/**
 * Initializes the Hierarchical Task View page.
 * @param {HTMLElement} wrapper - The parent element for the page.
 */
frappe.pages['hierarchical-task-view'].on_page_load = function(wrapper) {
    const page = frappe.ui.make_app_page({
        parent: wrapper,
        title: 'Hierarchical Task View',
        single_column: true
    });

    // Add a field for selecting the project
    let project_field = page.add_field({
        label: 'Project',
        field_name: 'project',
        field_type: 'Link',
        options: 'Project',
        change: () => {
            let project = project_field.get_value();
            if (project) {
                fetch_and_render_tasks(page, project);
            } else {
                // Clear the view if no project is selected
                $(page.body).find('.task-tree-container').html('<p class="text-muted">Please select a project to view its tasks.</p>');
            }
        }
    });
    
    // Add a container for our task tree
    $(page.body).append('<div class="task-tree-container" style="margin-top: 20px;"></div>');
    $(page.body).find('.task-tree-container').html('<p class="text-muted">Please select a project to view its tasks.</p>');
}

/**
 * Fetches tasks for a given project and renders them as a tree.
 * @param {object} page - The Frappe page object.
 * @param {string} project - The name of the project to fetch tasks for.
 */
function fetch_and_render_tasks(page, project) {
    const container = $(page.body).find('.task-tree-container');
    container.html('<p class="text-muted">Loading tasks...</p>');

    frappe.call({
        method: 'erpnext_enhancements.task_enhancements.doctype.hierarchical_task_view.hierarchical_task_view.get_project_tasks_hierarchy',
        args: {
            project: project
        },
        callback: function(r) {
            container.empty(); // Clear loading message
            if (r.message && r.message.length > 0) {
                let tree_html = render_task_tree(r.message);
                container.html(tree_html);
            } else {
                container.html('<p class="text-muted">No tasks found for this project.</p>');
            }
        }
    });
}

/**
 * Renders a list of tasks into an HTML tree structure.
 * @param {Array<object>} tasks - An array of task objects, potentially nested.
 * @returns {string} The HTML string representing the task tree.
 */
function render_task_tree(tasks) {
    // Start the tree with a top-level <ul>
    let html = '<ul class="task-tree">';

    tasks.forEach(task => {
        html += render_task_node(task);
    });

    html += '</ul>';
    return html;
}

/**
 * Recursively renders a single task and its children as an HTML list item.
 * @param {object} task - The task object to render.
 * @returns {string} The HTML string for the task node.
 */
function render_task_node(task) {
    // Each task is an <li>
    let node_html = `
        <li>
            <div class="task-item">
                <a href="/app/task/${task.name}">${task.subject}</a>
                <span class="badge bg-secondary">${task.status}</span>
            </div>
    `;

    // If the task has children, recursively render them in a nested <ul>
    if (task.children && task.children.length > 0) {
        node_html += '<ul>';
        task.children.forEach(child => {
            node_html += render_task_node(child);
        });
        node_html += '</ul>';
    }

    node_html += '</li>';
    return node_html;
}
