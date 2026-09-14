"""The NCR and Quality Action state machines, in one place.

These vocabularies exist in **two** places that nothing keeps in sync: the ``options`` string of
a Property Setter fixture, and the Python that writes a status. If they disagree the doctype
does not misbehave subtly — it stops saving entirely, because an off-options Select value fails
``_validate_selects`` and no ``ignore_*`` flag bypasses it.

So they are defined here once, ``tests/test_quality_lifecycle.py`` checks the fixture against
them, and neither can move without the other.

Why the Property Setter and the class override are one change
--------------------------------------------------------------

ERPNext's entire ``QualityAction`` controller, verified against ``origin/version-16``, is:

    def validate(self):
        self.status = "Open" if any([d.status == "Open" for d in self.resolutions]) else "Completed"

Two consequences, and both are fatal to the design this module implements:

* An action with **no resolution rows** — which is exactly what a punch-list item is — evaluates
  ``any([])`` as ``False`` and saves as **Completed**. Every punch item would be born closed.
* Once the options below replace core's ``Open / Completed``, that same line writes the literal
  ``"Completed"``, which is no longer an option, so **every** save of the doctype raises.

That is why ``override_doctype_class["Quality Action"]`` is not an optimisation but a
precondition, and why the two ship in the same commit. See ADR-0012.

Imports no ``frappe``: the state machine is the part worth asserting, and there is no Frappe
integration-test job in CI.
"""

# --------------------------------------------------------------------------- Quality Action

ACTION_OPEN = "Open"
ACTION_IN_PROGRESS = "In Progress"
ACTION_PM_RESOLVED = "PM Resolved"
ACTION_VERIFIED = "Verified at Next Inspection"
ACTION_CLOSED = "Closed"

#: Order matters: it is the order the Select offers, and it reads as the life of the record.
ACTION_STATUSES = (
	ACTION_OPEN,
	ACTION_IN_PROGRESS,
	ACTION_PM_RESOLVED,
	ACTION_VERIFIED,
	ACTION_CLOSED,
)

#: Written only by the re-verification path (sub-phase F) or by a person. `_derive_status`
#: never moves a record out of one of these, because "somebody confirmed this" is not a
#: conclusion that should be reachable by editing a child table.
ACTION_TERMINAL = (ACTION_VERIFIED, ACTION_CLOSED)

#: Core's literal, which this app never writes. Kept named so the migration and the defensive
#: normalisation below both refer to the same thing rather than repeating a magic string.
CORE_COMPLETED = "Completed"

# ------------------------------------------------------------------------- Non Conformance

NCR_OPEN = "Open"
NCR_ACTION_ASSIGNED = "Action Assigned"
NCR_PM_RESOLVED = "PM Resolved"
NCR_VERIFIED = "Verified"
NCR_CLOSED = "Closed"

NCR_STATUSES = (
	NCR_OPEN,
	NCR_ACTION_ASSIGNED,
	NCR_PM_RESOLVED,
	NCR_VERIFIED,
	NCR_CLOSED,
)

#: Core ships `Open / Resolved / Cancelled`. Both of the ones that disappear are mapped rather
#: than dropped, so a site that somehow has rows does not end up with unsaveable ones.
NCR_LEGACY_MAP = {"Resolved": NCR_VERIFIED, "Cancelled": NCR_CLOSED}

ACTION_LEGACY_MAP = {CORE_COMPLETED: ACTION_CLOSED}

SEVERITIES = ("Minor", "Major", "Critical")

#: The one that pages people. Held as a constant because the alerting in sub-phase G keys on it
#: and a typo there would mean the alert simply never fires, with nothing to see.
SEVERITY_CRITICAL = "Critical"

RESPONSIBLE_PARTIES = ("Internal Crew", "Subcontractor", "Design Error", "Material Defect")

ACTION_SOURCES = (
	"NCR",
	"Quality Review",
	"Quality Meeting",
	"Quality Feedback",
	"Punch List",
)

PRIORITIES = ("Low", "Medium", "High", "Critical")


def options_string(values):
	"""A Select's ``options`` as Frappe stores it: newline-separated, no leading blank."""
	return "\n".join(values)


def escalate(priority):
	"""One step up the priority ladder, capped at the top.

	Used when a fix fails re-verification (sub-phase F). Capped rather than wrapping, because a
	Critical item quietly becoming Low on its second failure is the opposite of what anybody
	would expect from the word "escalate".
	"""
	if priority not in PRIORITIES:
		return "Medium"
	index = PRIORITIES.index(priority)
	return PRIORITIES[min(index + 1, len(PRIORITIES) - 1)]


def derive_action_status(current, resolutions):
	"""The status a Quality Action should hold, given its resolution rows.

	This is the whole of what replaces core's one-liner, and the three rules it differs on are
	each deliberate:

    1. **A terminal status is never moved.** Verified and Closed mean somebody confirmed the
       fix, and editing a child table is not confirmation.
    2. **No resolution rows means "no information", not "done".** Core reads an empty table as
       Completed; a punch-list item has an empty table and is emphatically not done.
    3. **Nothing outside :data:`ACTION_STATUSES` is ever written** — including core's
       ``"Completed"``, which is mapped to Closed rather than stored, so a row that arrives
       holding it becomes saveable instead of raising forever.
	"""
	current = ACTION_LEGACY_MAP.get(current, current)
	if current in ACTION_TERMINAL:
		return current

	rows = resolutions or []
	if not rows:
		return current if current in ACTION_STATUSES else ACTION_OPEN

	still_open = any((getattr(r, "status", None) or (r.get("status") if isinstance(r, dict) else None)) == "Open" for r in rows)
	if still_open:
		return ACTION_IN_PROGRESS
	return ACTION_PM_RESOLVED
