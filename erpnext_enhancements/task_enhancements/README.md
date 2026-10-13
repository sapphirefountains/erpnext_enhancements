# Task Enhancements

Overrides ERPNext's core **Task** controller (via `override_doctype_class`) to keep the task tree valid, and adds an in-form descendant-task table.

> The standalone **Hierarchical Task View** desk page was retired in v1.623.0: Frappe v16's TreeView builds its own page and calls `get_tree_nodes`, so the page's data feed never ran and the tree never loaded. The Project Planner covers browsing a project's tasks. `patches/custom_pages_s1_navigation.py` deletes the Page record and every workspace or sidebar row that pointed at it.

> Recurring-task generation is **not** here — it lives in the repo-root [`tasks.py`](../../erpnext_enhancements/tasks.py) (`generate_next_task`, wired via `Task` `on_update`).

## File map

| File | Purpose | Key functions / classes |
|---|---|---|
| `doctype/task/task.py` | Core Task override + child-task HTML feed | `Task(BaseTask)` (`before_save`, `on_update`); whitelisted `get_child_tasks_html`; helpers `_get_all_descendants`, `_build_task_tree_html` |
| `doctype/task/task.js` | Form script: render the child-task tree on refresh | `frappe.ui.form.on("Task", {refresh})`, `add_toggle_functionality` |

## The Task override

`Task` subclasses `erpnext.projects.doctype.task.task.Task` and is registered in `hooks.py`:

```python
override_doctype_class = {"Task": "erpnext_enhancements.task_enhancements.doctype.task.task.Task"}
```

- `before_save` — promotes the task's `parent_task` to a **group** (`is_group=1`, saved with `ignore_permissions`) so the NestedSet tree stays valid. It does **not** chain `super()`.
- `on_update` — simply calls `super().on_update()`.

## Data flow

- **In-form child table:** `get_child_tasks_html` walks all descendants of a group task, batch-resolves assignees from ToDo rows, rebuilds the hierarchy in memory, and returns styled collapsible HTML into the form's `custom_child_tasks_table` HTML field. `task.js` renders it on refresh.

## `hooks.py` touchpoints

- `override_doctype_class` — Task (above).
- `doctype_js["Task"]` includes `task_enhancements/doctype/task/task.js` (alongside the comments + gantt scripts).
- `doc_events["Task"]` adds before_save/after_insert/on_update/on_trash handlers from `script_migrations.task` and `tasks.generate_next_task` (separate from the override).
- `fixtures`: `Task-custom_comments_tab`/`field` and `Task-custom_create_child_task_btn` Custom Fields.

## Gotchas

- `task.js` / `task.py` contain some left-in debug logging.
- Descendant collection in `_get_all_descendants` is **one query per node** (not batched) — watch performance on very deep trees.
- The `custom_child_tasks_table` HTML field is assumed to exist on Task but is not in the `fixtures` list.
