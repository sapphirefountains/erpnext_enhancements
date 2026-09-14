"""Seed the five roles the Quality module's approval chain names (v1.446.0, WI-075).

The build spec's approval chain names President, Production Manager, Account Executive,
Quality Inspector and Controller. **None of them exists on this site.** Prod carries the
``* Team`` roles plus ``Project Manager`` and ``Quality Manager``, and the spec's vocabulary
was never entered.

Why a patch and not ``fixtures/role.json``
------------------------------------------

Fixture files import in **alphabetical filename order** (``frappe/utils/fixtures.py`` sorts the
directory listing), so ``custom_docperm.json`` lands before ``role.json``. A DocPerm row naming
a Role that does not exist yet is exactly the kind of dangling record this app has been bitten
by before. ``post_model_sync`` patches run before fixture sync, which closes it. Same reasoning
as ``seed_po_creator_role``, ``seed_training_roles`` and ``seed_hr_team_role``.

Creating a Role is not the same as granting it
----------------------------------------------

Do **not** hand any of these to a user directly. ``populate_role_profile_roles`` rebuilds a
profiled user's roles from the union of their Role Profiles on *every* User save, so a direct
grant is wiped the next time anybody edits that user. Grant through a Role Profile.

One live consequence, and it is the reason this patch has a long docstring
--------------------------------------------------------------------------

``crm_enhancements/handoff.py:224`` documents that ``Account Executive`` is *"a Select value on
Process Step Template, not a real Role on this site"*, and ``_role_holder_emails`` skips a Role
that does not exist rather than raising. **Creating the Role arms that path.** From here on, a
``Hand-Off Attendee Role`` row naming ``Account Executive`` will resolve to its holders and mail
them.

Verified on production 2026-09-14 that this is **latent, not live**: all three configured
attendee rows use explicit group addresses (sales@, production@, billing@) with ``role`` null,
so nothing about hand-off attendee resolution changes today. It becomes live the first time
somebody sets ``role`` on one of those rows — which is a deliberate act with a visible effect,
and is the behaviour that field was built for.

Insert-only and idempotent: a role that already exists is left exactly as it is, including a
``desk_access`` somebody has since changed on purpose.
"""

import frappe

#: Role name -> why it exists. The reason is not decoration: a role nobody can account for is a
#: role nobody dares remove, and this app has a standing rule against hand-querying tabDocPerm
#: to decide whether one is safe to delete.
QUALITY_ROLES = {
	"President": "Scope of Work final approval and lock; Critical NCR alert recipient; "
	"approval for any budget reallocation touching General Conditions, Contingency or Fee.",
	"Production Manager": "Owns Quality Goals, Quality Review and the Quality Meeting; "
	"Critical NCR alert recipient.",
	"Account Executive": "Marks the lead Won and attends the hand-off meeting where scope "
	"and project-specific quality goals are authored.",
	"Quality Inspector": "Authorised to sign off a Quality Inspection instance.",
	"Controller": "Second approval, with the President, on protected-category budget movement.",
}


def execute() -> None:
	created = []
	for role_name in QUALITY_ROLES:
		if frappe.db.exists("Role", role_name):
			continue
		role = frappe.new_doc("Role")
		role.role_name = role_name
		role.desk_access = 1
		role.insert(ignore_permissions=True)
		created.append(role_name)

	# Log the count rather than staying silent. A patch that matched nothing commits and
	# records itself in `tabPatch Log` indistinguishably from one that did the work, so the
	# deploy log is the only witness that tells the two apart.
	named = ", ".join(created) or "none — all present"
	frappe.logger().info(
		f"seed_quality_roles: created {len(created)} of {len(QUALITY_ROLES)} roles ({named})"
	)
