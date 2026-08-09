"""Seed the "Chat User" and "Chat Auditor" roles.

**Why a patch and not a fixture.** Fixtures import in *alphabetical filename order*
(`frappe/utils/fixtures.py` sorts the directory), so `role.json` lands before
`role_profile.json` — but both land **after** post_model_sync patches run. Any Role a
fixture references must therefore already exist, which is why "PO Approver" and
"PO Creator" are seeded by `patches/seed_po_approver_role.py` /
`patches/seed_po_creator_role.py` and are deliberately absent from the `Role` fixture
entry at `hooks.py:1047-1049`. These two follow that precedent exactly. A second reason
sits on top of it: a Role Profile shipped as a fixture has crashed `bench migrate` on this
site before with `DocumentLockedError` (fixed in v1.171.1), and the patch route does not
re-enter that failure mode.

**Why this patch does not assign the roles to anybody — the expensive part to rediscover.**
`User.populate_role_profile_roles` regenerates a user's `roles` child table from the union
of their Role Profiles on **every save**. So a role granted directly to a *profiled* user is
wiped the next time anything saves that user, silently and with no error — and the grant
looks like it worked right up until it disappears. A chat role reaches a profiled user only
through a Role Profile. The symmetric trap is just as expensive: assigning a Role Profile to
a user who has none regenerates their roles *from that profile*, wiping System Manager.

Both of those are rollout decisions with a blast radius, and Phase 1 ships chat dormant
(`Chat Settings.enabled = 0`, `restrict_to_whitelist = 1`, empty allow-list, no UI). So this
patch creates the roles and stops, exactly as `seed_po_approver_role` does — that one's
docstring ends *"assign it to the CEO post-deploy"*. Attachment is done deliberately, by the
rollout, through a Role Profile:

    bench --site <site> execute \\
        erpnext_enhancements.patches.seed_chat_roles.attach_role_to_profile \\
        --kwargs '{"role": "Chat User", "profile": "Projects & Operations"}'

**This command has not been run.** It requires a bench, and no bench exists in the
environment this patch was written in.

What the roles are for:

* **Chat User** — the one DocPerm in the whole chat module. `Chat Room` grants it `read`
  and nothing else (ADR §H.4.2); every other chat DocType ships with an empty `permissions`
  array on purpose (§F.18.1 Layer 1). That single row is what makes `doc_subscribe` on
  `doc:Chat Room/<name>` reach `chat.permissions.chat_room_has_permission` at all, which is
  the realtime security boundary. Row-level scoping is membership, not the role.
* **Chat Auditor** — decision #12's oversight role: read chat you are not a participant in.
  It grants nothing by existing. It becomes live only when a System Manager names it in
  `Chat Settings.admin_oversight_role`, which **ships blank so the hatch fails closed**, and
  Phase 6 adds the audit write for every non-participant read
  (`chat.permissions.note_privileged_read`).

Insert-only and idempotent, like every other role patch here.
"""

import frappe

#: ``role_name`` → why it exists. Both get desk access: `Chat Room`'s DocPerm is evaluated
#: by the desk permission stack (which is also what the socket join calls back into), and a
#: role with ``desk_access = 0`` is a website-only role there.
CHAT_ROLES: tuple[tuple[str, str], ...] = (
	("Chat User", "Participate in ERPNext chat — read scoped to room membership."),
	("Chat Auditor", "Oversight read of chat the holder is not a participant in (audited)."),
)


def execute() -> None:
	"""Create any of :data:`CHAT_ROLES` that does not exist yet. Never assigns them."""
	for role_name, _why in CHAT_ROLES:
		if frappe.db.exists("Role", role_name):
			continue
		role = frappe.new_doc("Role")
		role.role_name = role_name
		role.desk_access = 1
		role.insert(ignore_permissions=True)


def attach_role_to_profile(role: str, profile: str) -> str:
	"""Add ``role`` to an existing Role Profile, idempotently. **Not called by the patch.**

	The deliberate rollout step, kept next to the reasoning that governs it rather than
	improvised in a console six months from now. It refuses to create the profile: a
	Role Profile that did not exist is one nobody holds, and creating one here would put a
	rollout artifact in a migration.

	Args:
		role: a role from :data:`CHAT_ROLES`.
		profile: the ``Role Profile`` name to add it to. It must already exist.

	Returns:
		A one-line human-readable result, so ``bench execute`` prints something useful.

	Raises:
		frappe.DoesNotExistError: if the role or the profile is not there — a typo'd
			profile name must fail rather than silently grant nothing.
	"""
	if not frappe.db.exists("Role", role):
		raise frappe.DoesNotExistError(f"Role {role!r} does not exist; run this patch first.")
	if not frappe.db.exists("Role Profile", profile):
		raise frappe.DoesNotExistError(f"Role Profile {profile!r} does not exist.")

	doc = frappe.get_doc("Role Profile", profile)
	# getattr(..., None) or []: this runs during migrate on sites where the child table
	# may not have been synced yet, and an AttributeError here aborts the whole rollout.
	existing = {(getattr(row, "role", None) or "") for row in (doc.get("roles") or [])}
	if role in existing:
		return f"{profile}: already has {role}"

	doc.append("roles", {"role": role})
	doc.save(ignore_permissions=True)
	return f"{profile}: added {role}"
