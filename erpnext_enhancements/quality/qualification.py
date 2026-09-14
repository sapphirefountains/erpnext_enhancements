# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Whether the person holding the clipboard was the person the template asked for.

WI-075 sub-phase H, and the decision recorded in the work item is that this is **advisory and
never blocks a save**. The numbers are the argument: the company has two Senior Technicians, no
Masters and one Project Manager, so a hard gate would routinely stop an inspection being
*recorded* rather than stop unqualified work being done. An inspection that happened and was not
written down is worse than one written down by the wrong person, because the second at least
leaves a trail somebody can question.

The rule, and the comparison that must never be made
-----------------------------------------------------

``Position`` is a tree carrying ``job_family`` and an integer ``tier``, so seniority is already
modelled and does not need inventing here: **within one job family a higher tier satisfies a
lower one.** A Master Technician can do a Senior Technician's inspection.

Across families it means nothing. Tier 3 Designer and tier 3 Technician are both threes and
nothing follows from that — so :func:`position_satisfies` requires the families to match
*before* it compares tiers. Note the direction this would fail in: a bare ``tier >= tier``
comparison **passes** a Designer as a qualified Technician, and a check that passes is a check
nobody investigates. That is the same shape as the trailing-space audit that reported clean on
broken data, and it is why the rule lives in a module CI can reach.

Where seniority is not modelled — either side missing a family or a tier — it falls back to an
exact name match, because an unmodelled hierarchy is not a flat one; it is an unknown one.

Three things it deliberately says nothing about
------------------------------------------------

**A template with no requirements produces no finding**, ever. Absence of a requirement is not a
failed requirement, and warning on every unconfigured template is the fastest way to teach
people to dismiss the warning — which costs the real ones too.

**An inspector with no Employee record is reported as unknown, not as unqualified.** The two are
different claims, and only one of them is true.

**It does not look at who is signing.** A template asks for the person carrying out the
inspection; countersignature is a different question and this module is not it.

Imports nothing. Every caller-facing shape below is a plain dict, so the frappe half can build
them from whatever query it likes and this half stays asserted on every push.
"""

#: Finding kinds. Kept as constants because the advisory's message, its fingerprint and its
#: tests all key on them, and a typo in any one of the three would silently change behaviour.
KIND_POSITION = "position"
KIND_COURSE = "course"
KIND_UNKNOWN = "unknown"


def has_requirements(template):
	"""Whether this template asks for anything at all.

	Checked first by every caller. A template nobody has configured is not a template somebody
	has failed.
	"""
	return bool(_get(template, "required_position") or _get(template, "required_course"))


def position_satisfies(required, held):
	"""Whether ``held`` meets ``required``. Both are position dicts or ``None``.

	A position dict carries ``name``, ``job_family`` and ``tier``. The family gate comes first
	and is not optional — see the module docstring for the failure it prevents.
	"""
	if not required:
		return True
	required_name = _get(required, "name")
	held_name = _get(held, "name") if held else None
	if not held_name:
		return False
	if held_name == required_name:
		return True

	required_family = _get(required, "job_family")
	held_family = _get(held, "job_family")
	if not required_family or required_family != held_family:
		# Different families, or a family nobody has filled in. Either way there is no
		# common scale to compare on, so the exact-match answer above is the only one.
		return False

	required_tier = _tier(required)
	held_tier = _tier(held)
	if required_tier is None or held_tier is None:
		# Same family, but seniority is not modelled on one of them. An unmodelled
		# hierarchy is unknown, not flat.
		return False
	return held_tier >= required_tier


def findings(template, inspector):
	"""What this template asked for that this inspector does not have.

	``inspector`` carries ``user``, ``full_name``, ``employee`` (or ``None``), ``position``
	(a position dict or ``None``) and ``valid_courses`` (an iterable of course names).

	Returns a list of finding dicts, in a fixed order so the fingerprint is stable. Empty means
	nothing to say — which is also what an unconfigured template returns.
	"""
	if not has_requirements(template):
		return []

	found = []
	required_position = _get(template, "required_position_doc") or None
	required_course = _get(template, "required_course")

	if not _get(inspector, "employee"):
		# "We cannot tell" is not "qualified". Said once, as its own kind, so the message can
		# name the actual problem: there is nobody in HR to compare against.
		found.append(
			{
				"kind": KIND_UNKNOWN,
				"requirement": _get(template, "required_position") or required_course or "",
				"held": "",
				"reason": "no Employee record, so nothing can be checked against",
			}
		)
		return found

	if required_position and not position_satisfies(required_position, _get(inspector, "position")):
		held = _get(inspector, "position")
		found.append(
			{
				"kind": KIND_POSITION,
				"requirement": _get(required_position, "name") or "",
				"held": _get(held, "name") if held else "",
				"reason": (
					"holds no position on record"
					if not held
					else "position does not meet the one this template asks for"
				),
			}
		)

	if required_course and required_course not in set(_get(inspector, "valid_courses") or ()):
		found.append(
			{
				"kind": KIND_COURSE,
				"requirement": required_course,
				"held": "",
				"reason": "course not completed, or the completion has lapsed",
			}
		)

	return found


def fingerprint(user, inspection_findings):
	"""A stable key for "this person, these findings", so repeat saves leave one comment.

	Order-independent: the caller should not be able to change the fingerprint by reordering a
	list, because then five saves of one unchanged inspection would leave five comments and the
	timeline becomes the noise the advisory was supposed to avoid being.
	"""
	parts = sorted(
		f"{f.get('kind', '')}:{f.get('requirement', '')}:{f.get('held', '')}"
		for f in inspection_findings or []
	)
	return "|".join([str(user or ""), *parts])


def summary_line(finding):
	"""One finding as a sentence fragment, for the inline warning and the comment alike."""
	requirement = finding.get("requirement") or ""
	reason = finding.get("reason") or ""
	if finding.get("kind") == KIND_COURSE:
		return f"{requirement} — {reason}"
	held = finding.get("held")
	if held:
		return f"{requirement} — {reason} (holds {held})"
	return f"{requirement} — {reason}"


def _tier(position):
	value = _get(position, "tier")
	if value in (None, ""):
		return None
	try:
		return int(value)
	except (TypeError, ValueError):
		return None


def _get(obj, field, default=None):
	if obj is None:
		return default
	if isinstance(obj, dict):
		return obj.get(field, default)
	return getattr(obj, field, default)
