"""Live progress for "Create Project in PM System" (v1.747.0).

Making a Project from an Opportunity runs as a background job on the ``long`` queue
(:func:`~erpnext_enhancements.crm_enhancements.api.create_project_from_opportunity_background`).
Until v1.747.0 the dialog said "started in the background" and went away, and the page changed
only if the realtime status happened to arrive. This module is what lets the person who clicked
watch it happen, on the Desk form and the guided page alike:

* :data:`STEPS` — the job's steps, in the order it runs them. The client draws its bar from
  these, so the bar is a count of finished steps, never a guess at time. The steps are not equal
  in length (making the project and its template tasks, and the Drive folder, are the slow ones),
  and the bar says "step 2 of 5" rather than pretending they are.
* :func:`mark_queued` / :func:`mark_step` / :func:`mark_finished` — the enqueue and the job
  record where they are in the cache and push it to the requester as the
  ``ee_project_creation_progress`` realtime event. The final outcome still goes out as
  ``project_creation_status``, which every surface already listens to.
* :func:`project_creation_state` (whitelisted) — the same state on request, with whether the RQ
  job still exists. The progress dialog asks when the events go quiet, and the create dialog asks
  before it opens, so a creation already under way is shown rather than started a second time.

Why the cache, and not only the events: every deploy FLUSHDBs both Redis instances (repo
``CLAUDE.md``), which deletes a queued job without a word, and a socket that drops for a moment
misses an event for good. "The state says queued or running, but RQ has no such job" is how a
lost creation is recognised (:func:`phase_of`), and the person is told it stopped and can be
started again. Starting again is safe: the job takes a row lock on the Opportunity and links a
Project that already points at it rather than making a second (see the job's docstring). Both
Redis instances are flushed together, so the state and the job vanish together, and a creation
lost to a deploy reads as "idle": the dialog then simply offers to create it, which is the truth.

Progress is advisory. Nothing here may stop a Project being made, so every write and push is
wrapped and a failure is logged, never raised.
"""

import frappe
from frappe import _

#: The realtime event carrying each step (the final outcome is ``project_creation_status``).
EVENT = "ee_project_creation_progress"
#: Long enough to outlive the job's own 1800 s timeout plus a wait in the queue.
STATE_TTL_SECONDS = 3600
#: RQ states in which the job will still run or is running.
ACTIVE_JOB_STATES = ("queued", "started", "deferred", "scheduled")

#: The job's steps, in the order it runs them. Keys are what the job reports; labels are what the
#: dialog shows (translated per request by :func:`steps`).
STEPS = (
	("check", "Checking the hand-off"),
	("project", "Creating the project and its tasks"),
	("attachments", "Copying the opportunity's attachments"),
	("link", "Linking the project to this opportunity"),
	("drive", "Creating the Google Drive folder"),
)
STEP_KEYS = tuple(key for key, _label in STEPS)


def steps():
	"""``[{"key", "label"}]`` for the dialog's list, translated for the current user."""
	return [{"key": key, "label": _(label)} for key, label in STEPS]


def job_id(opportunity_name):
	"""The one RQ job id per Opportunity, so ``frappe.enqueue(deduplicate=True)`` refuses a second
	job while the first is queued or running (a double click, or two people on one deal)."""
	return f"ee-project-from-opportunity-{opportunity_name}"


def _key(opportunity_name):
	return f"ee:project_creation:{opportunity_name}"


def read_state(opportunity_name):
	"""The last state the enqueue or the job recorded, or ``None``."""
	try:
		state = frappe.cache.get_value(_key(opportunity_name))
	except Exception:
		return None
	return state if isinstance(state, dict) else None


def _record(opportunity_name, user, state):
	"""Cache ``state`` and push it to ``user``. Never raises: progress must not stop the job."""
	state = dict(state, opportunity=opportunity_name, user=user, total=len(STEPS))
	try:
		frappe.cache.set_value(_key(opportunity_name), state, expires_in_sec=STATE_TTL_SECONDS)
	except Exception:
		frappe.log_error(
			title="Project creation progress: cache write failed", message=frappe.get_traceback()
		)
	if user:
		try:
			frappe.publish_realtime(EVENT, state, user=user)
		except Exception:
			frappe.log_error(
				title="Project creation progress: publish failed", message=frappe.get_traceback()
			)
	return state


def mark_queued(opportunity_name, user):
	"""The job is in the queue and no worker has started it yet."""
	return _record(opportunity_name, user, {"phase": "queued", "step": None, "done": 0, "result": None})


def mark_step(opportunity_name, user, step):
	"""The job has started ``step``; every step before it is done."""
	done = STEP_KEYS.index(step) if step in STEP_KEYS else 0
	return _record(opportunity_name, user, {"phase": "running", "step": step, "done": done, "result": None})


def mark_finished(opportunity_name, user, result, step=None):
	"""The job is over. ``result`` is ``{"status", "project", "reason", ...}``; ``step`` is the step
	it stopped on when it did not succeed. Cached only: the job's own ``project_creation_status``
	broadcast is the push, and two terminal events would only be two chances to say it twice."""
	status = (result or {}).get("status")
	done = (
		len(STEPS) if status in ("success", "exists") else (STEP_KEYS.index(step) if step in STEP_KEYS else 0)
	)
	state = {
		"phase": "finished",
		"step": step,
		"done": done,
		"result": result,
		"opportunity": opportunity_name,
		"user": user,
		"total": len(STEPS),
	}
	try:
		frappe.cache.set_value(_key(opportunity_name), state, expires_in_sec=STATE_TTL_SECONDS)
	except Exception:
		frappe.log_error(
			title="Project creation progress: cache write failed", message=frappe.get_traceback()
		)
	return state


def job_status(opportunity_name):
	"""The RQ job's status as a string, ``None`` when there is no such job (finished and expired,
	never queued, or flushed by a deploy), or ``"unknown"`` when the queue could not be asked."""
	try:
		from frappe.utils.background_jobs import get_job_status

		status = get_job_status(job_id(opportunity_name))
	except Exception:
		return "unknown"
	if status is None:
		return None
	return str(getattr(status, "value", status))


def phase_of(state, job, project):
	"""Where a creation stands, from the cached ``state``, the RQ ``job`` status and the
	Opportunity's linked ``project``. One of:

	* ``queued`` / ``running`` — the job exists and will run or is running;
	* ``finished`` — the job recorded how it ended (``state["result"]`` says how);
	* ``done`` — the Opportunity has its Project and nothing is running;
	* ``lost`` — the state says a job was on its way, but the queue has no such job: a deploy or a
	  worker restart took it. Nothing was linked, so it can be started again;
	* ``idle`` — nothing is known: no creation was asked for, or what was known has expired.
	"""
	recorded = (state or {}).get("phase")
	if job in ACTIVE_JOB_STATES:
		return "running" if recorded == "running" else "queued"
	if recorded == "finished":
		return "finished"
	if project:
		return "done"
	if recorded in ("queued", "running"):
		# The queue could not be asked: say what was last known rather than call it lost.
		return recorded if job == "unknown" else "lost"
	return "idle"


def creation_state(opportunity_name):
	""":func:`project_creation_state` without the permission check, for callers that have done it."""
	state = read_state(opportunity_name)
	job = job_status(opportunity_name)
	project = frappe.db.get_value("Opportunity", opportunity_name, "custom_created_project")
	return {
		"opportunity": opportunity_name,
		"phase": phase_of(state, job, project),
		"project": project or None,
		"job": job,
		"state": state,
		"steps": steps(),
	}


@frappe.whitelist()
def project_creation_state(opportunity_name):
	"""Where the project creation for ``opportunity_name`` stands (see :func:`phase_of`).

	Asked by the create dialog before it opens and by the progress dialog when the realtime events
	go quiet. Read access is gated on the Opportunity.
	"""
	if not frappe.has_permission("Opportunity", "read", doc=opportunity_name):
		frappe.throw(_("Not permitted to read this Opportunity."), frappe.PermissionError)
	return creation_state(opportunity_name)
