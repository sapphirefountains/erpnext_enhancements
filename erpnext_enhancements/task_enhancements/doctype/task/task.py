# File: erpnext_enhancements/erpnext_enhancements/task/task.py

"""Custom **Task** controller that overrides ERPNext's core Task class.

This module replaces ``erpnext.projects.doctype.task.task.Task`` via the
``override_doctype_class`` entry in hooks.py::

    override_doctype_class = {
        "Task": "erpnext_enhancements.task_enhancements.doctype.task.task.Task",
    }

The override subclasses the core controller (``BaseTask``) so all stock Task
behaviour is preserved, adding only one rule: whenever a task is given a
parent, that parent is automatically promoted to a "group" (``is_group = 1``).
This keeps ERPNext's NestedSet tree consistent so tasks can be browsed as a
hierarchy (see the Hierarchical Task View desk page and the in-form child-task
table rendered by ``get_child_tasks_html``).

NOTE: recurring-task generation lives elsewhere (``tasks.py`` at the app root,
wired through ``doc_events``), not in this controller.
"""

import frappe
from erpnext.projects.doctype.task.task import Task as BaseTask

class Task(BaseTask):
    """ERPNext Task controller override (installed via override_doctype_class).

    Extends the stock controller; only the lifecycle hooks below are
    customised, everything else is inherited from ``BaseTask``.
    """

    def before_save(self):
        """Promote the parent task to a group before saving.

        Overrides the core hook. If this task points at a ``parent_task`` that
        is not yet flagged ``is_group``, load the parent and set ``is_group = 1``
        (saved with ``ignore_permissions`` so the cascade succeeds regardless
        of the editing user's rights on the parent). This guarantees parents of
        sub-tasks are valid tree groups.

        Note: the core ``before_save`` does not do meaningful work, so it is not
        chained here; ``on_update`` is where the parent ``super()`` call lives.
        """
        if self.parent_task:
            if not frappe.db.get_value("Task", self.parent_task, "is_group"):
                parent_task_doc = frappe.get_doc("Task", self.parent_task)
                parent_task_doc.is_group = 1
                parent_task_doc.save(ignore_permissions=True)

    def on_update(self):
        """Defer entirely to the core Task ``on_update`` (NestedSet upkeep, etc.).

        Overridden only to make the override explicit; it adds no behaviour and
        simply calls ``super().on_update()``.
        """
        super(Task, self).on_update()

@frappe.whitelist()
def get_child_tasks_html(task_name):
    """Render the full descendant tree of a task as an HTML table.

    Whitelisted endpoint called from ``task.js`` on form refresh (only when the
    task ``is_group`` and is not new). It walks every descendant of
    ``task_name``, attaches each descendant's assignee (resolved from ToDo
    rows in one batched query), rebuilds the parent/child hierarchy in memory,
    and returns a styled, collapsible HTML tree injected into the form's
    ``custom_child_tasks_table`` HTML field.

    Returns an empty string when there are no descendants; on error it logs the
    traceback and returns a short error ``<div>`` rather than raising.
    """
    # Use a dedicated logger for debugging
    logger = frappe.logger("erpnext_enhancements")
    logger.debug(f"Executing get_child_tasks_html for task: {task_name}")

    try:
        task = frappe.get_doc("Task", task_name)
        descendants = _get_all_descendants("Task", task.name)
        
        logger.debug(f"Found {len(descendants)} descendants.")
        if not descendants:
            logger.debug("No descendants found. Returning empty string.")
            return ""

        # Get all task names for the assignment query
        task_names = [d.name for d in descendants]

        # Fetch all assignments for the descendant tasks in a single query
        assignments = frappe.get_all(
            "ToDo",
            filters={"reference_name": ("in", task_names), "reference_type": "Task"},
            fields=["reference_name", "allocated_to"]
        )

        # Create a dictionary mapping task names to their assigned user
        assignment_map = {a.reference_name: a.allocated_to for a in assignments}

        # Add the 'allocated_to' field to each descendant task
        for d in descendants:
            d.allocated_to = assignment_map.get(d.name)

        # A dictionary to hold tasks by their name for easy lookup
        task_map = {d.name: d for d in descendants}
        # Initialize a children list for each task
        for d in descendants:
            d.children = []

        # This list will hold the top-level tasks in the hierarchy below the main task
        tree = []
        for d in descendants:
            if d.parent_task == task_name:
                tree.append(d)
            elif d.parent_task in task_map:
                task_map[d.parent_task].children.append(d)
        
        logger.debug(f"Constructed a tree with {len(tree)} top-level children.")

        tree_html = _build_task_tree_html(tree, level=0)
        if not tree_html:
            return ""

        header_html = """
        <style>
            .task-tree-container { padding: 10px; }
            .task-tree-header, .task-tree-row {
                display: flex;
                align-items: center;
                padding: 8px;
                border-bottom: 1px solid #d1d8dd;
            }
            .task-tree-header { font-weight: bold; background-color: #f7fafc; }
            .task-tree-col {
                flex-grow: 1; flex-basis: 0; padding: 0 8px;
                white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
            }
            .task-tree-col-subject { flex-grow: 2.5; }
            .task-tree-col-status { flex-grow: 0.8; }
            .task-tree-col-user { flex-grow: 1.2; }
            .task-tree-col-date { flex-grow: 1; }
            .task-tree-col-time { flex-grow: 0.7; }
            .task-tree-container ul { list-style-type: none; padding-left: 0; margin: 0; }
            .task-tree-container li > ul { padding-left: 0; }
            .task-tree-row a { font-weight: 500; }
            .task-tree-row i { margin-right: 5px; width: 14px; text-align: center; }
        </style>
        <div class="task-tree-container">
            <div class="task-tree-header">
                <div class="task-tree-col task-tree-col-subject">Subject</div>
                <div class="task-tree-col task-tree-col-status">Status</div>
                <div class="task-tree-col task-tree-col-user">Assigned To</div>
                <div class="task-tree-col task-tree-col-date">Start Date</div>
                <div class="task-tree-col task-tree-col-date">End Date</div>
                <div class="task-tree-col task-tree-col-time">Time (hrs)</div>
            </div>
        """
        
        html_output = header_html + tree_html + "</div>"
        logger.debug(f"Generated HTML (first 200 chars): {html_output[:200]}")
        return html_output

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Task Enhancements Error")
        return f"<div>An error occurred: {e}</div>"


def _get_all_descendants(doctype, parent):
    """Recursively collect every descendant task under ``parent``.

    Performs a depth-first walk over ``parent_task`` links, returning a flat
    list of ``frappe._dict`` rows (each carrying the fields needed to render
    the tree). The dict wrapper enables dot-notation access by the
    tree-building logic. Note: one query is issued per node, so very deep/wide
    trees fan out into many small queries.
    """
    descendants = []
    fields = [
        "name", "subject", "parent_task", "status",
        "exp_start_date", "exp_end_date", "expected_time"
    ]
    children = frappe.get_all(doctype, filters={'parent_task': parent}, fields=fields)

    for child in children:
        # Convert the dictionary to a Frappe dict to allow dot notation access,
        # making it compatible with the existing tree-building logic.
        child_obj = frappe._dict(child)
        descendants.append(child_obj)
        descendants.extend(_get_all_descendants(doctype, child_obj.name))

    return descendants


def _build_task_tree_html(tasks, level=0):
    """Recursively render a list of task nodes (and their children) as nested <ul>/<li>.

    Each node becomes a flex row (subject link, status, assignee, dates,
    expected time); ``level`` drives the subject indentation. Nodes that have
    children get a clickable toggle icon (wired up client-side in task.js) and
    recurse one level deeper. Returns an empty string for an empty list.
    """
    if not tasks:
        return ""

    html = "<ul>"
    for task in tasks:
        html += "<li>"

        # Use helper to avoid None display
        status = task.status or ""
        assigned_to = task.allocated_to or ""
        start_date = task.exp_start_date or ""
        end_date = task.exp_end_date or ""
        expected_time = task.expected_time or ""

        # Check for children more robustly
        has_children = hasattr(task, 'children') and task.children

        toggle_icon = '<i class="fa fa-minus-square toggle-child-tasks" style="cursor: pointer;"></i> ' if has_children else '<i class="fa fa-square-o"></i> '

        # Calculate indentation for the subject
        indentation_style = f"padding-left: {level * 15}px;"

        html += f"""
        <div class="task-tree-row">
            <div class="task-tree-col task-tree-col-subject">
                <div style="{indentation_style}">
                    {toggle_icon}
                    <a href="/app/task/{task.name}">{task.subject}</a>
                </div>
            </div>
            <div class="task-tree-col task-tree-col-status">{status}</div>
            <div class="task-tree-col task-tree-col-user">{assigned_to}</div>
            <div class="task-tree-col task-tree-col-date">{start_date}</div>
            <div class="task-tree-col task-tree-col-date">{end_date}</div>
            <div class="task-tree-col task-tree-col-time">{expected_time}</div>
        </div>
        """

        if has_children:
            html += _build_task_tree_html(task.children, level + 1)
            
        html += "</li>"
    html += "</ul>"
    return html
