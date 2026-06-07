import frappe

@frappe.whitelist()
def get_project_tasks_hierarchy(project):
    """
    Fetches all tasks for a given project and returns them in a hierarchical
    JSON structure suitable for tree rendering.

    Args:
        project (str): The name of the project for which to fetch tasks.

    Returns:
        list[dict]: A list of root-level task dictionaries, where each task
                    may contain a 'children' key with a list of its sub-tasks.
                    Returns an empty list if the project is not found or has no tasks.
    """
    if not project:
        return []

    # Fetch all tasks for the project in a single query for efficiency.
    tasks = frappe.get_all(
        "Task",
        filters={"project": project},
        fields=["name", "subject", "status", "parent_task", "start_date", "end_date"]
    )

    if not tasks:
        return []

    # Create a dictionary for quick access to each task by its name (ID).
    task_map = {task['name']: task for task in tasks}

    # Initialize a 'children' list for each task. This is where we'll nest sub-tasks.
    for task in tasks:
        task['children'] = []

    # The root nodes of our tree.
    root_tasks = []

    # Iterate through the tasks to build the hierarchy.
    for task in tasks:
        parent_id = task.get('parent_task')
        # Check if the task has a parent AND if that parent exists in our map.
        if parent_id and parent_id in task_map:
            # This is a sub-task. Append it to its parent's 'children' list.
            parent_task = task_map[parent_id]
            parent_task['children'].append(task)
        else:
            # This is a top-level task (a root node).
            root_tasks.append(task)

    # Optional: Sort children tasks within each parent, e.g., by start date.
    def sort_children_recursively(task_list):
        for task in task_list:
            if task['children']:
                # Sort children by start_date, handling cases where it might be None.
                task['children'].sort(key=lambda x: x.get('start_date') or frappe.utils.nowdate())
                sort_children_recursively(task['children'])
    
    sort_children_recursively(root_tasks)
    
    # Sort the top-level tasks as well.
    root_tasks.sort(key=lambda x: x.get('start_date') or frappe.utils.nowdate())

    return root_tasks
