"""Backend for the **Hierarchical Task View** desk page.

Provides the data feed for the tree shown on the ``hierarchical-task-view``
desk page (``hierarchical_task_view.js``). The page lets a user pick a Project
and then browses that project's tasks as an expandable tree built from the
``parent_task`` relationships.
"""

import frappe

def format_tasks_for_tree(tasks):
    """Recursively formats a list of tasks for the Frappe Tree View component.

    Maps each task into the shape the front-end ``frappe.views.TreeView``
    expects: ``value`` (task name / node id), ``label`` (subject),
    ``expandable`` (1 if it has children, else 0), and a recursively-formatted
    ``children`` list. Leaf nodes get an empty ``children`` list.
    """
    formatted_list = []
    for task in tasks:
        # Determine if the node is expandable (a parent)
        is_expandable = 1 if task.get('children') else 0

        formatted_list.append({
            "value": task.get("name"),
            "label": task.get("subject"),
            "expandable": is_expandable,
            # Pass children to be formatted recursively if they exist
            "children": format_tasks_for_tree(task.get('children', [])) if is_expandable else []
        })
    return formatted_list

@frappe.whitelist()
def get_project_tasks_hierarchy(project):
    """Fetch all tasks for a project as a hierarchical tree for the desk page.

    Whitelisted endpoint invoked by the page's ``get_nodes`` callback. It loads
    every Task on the project in one query, links them into a parent/child tree
    in memory via ``parent_task`` (tasks whose parent is absent or outside the
    project become roots), sorts each level by ``exp_start_date`` (undated tasks
    sort as if starting today), and returns the tree formatted for
    ``frappe.views.TreeView`` (see ``format_tasks_for_tree``). Returns an empty
    list when no project is given or the project has no tasks.
    """
    if not project:
        return []

    tasks = frappe.get_all(
        "Task",
        filters={"project": project},
        fields=["name", "subject", "status", "parent_task", "exp_start_date", "exp_end_date"]
    )

    if not tasks:
        return []

    task_map = {task['name']: task for task in tasks}
    for task in tasks:
        task['children'] = []

    root_tasks = []
    for task in tasks:
        parent_id = task.get('parent_task')
        if parent_id and parent_id in task_map:
            parent_task = task_map[parent_id]
            parent_task['children'].append(task)
        else:
            root_tasks.append(task)
            
    # Optional: Sort tasks by start date
    def sort_recursively(task_list):
        for task in task_list:
            if task['children']:
                task['children'].sort(key=lambda x: x.get('exp_start_date') or frappe.utils.nowdate())
                sort_recursively(task['children'])
    sort_recursively(root_tasks)
    root_tasks.sort(key=lambda x: x.get('exp_start_date') or frappe.utils.nowdate())

    # Return the data in the format required by the tree view
    return format_tasks_for_tree(root_tasks)
