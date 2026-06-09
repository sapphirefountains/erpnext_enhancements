/**
 * Quill @-mentions in rich-text editors.
 *
 * Targets: every Frappe `ControlTextEditor` (rich-text field) across the desk.
 * Loaded via: hooks.py `app_include_js` (global).
 *
 * Enables the Quill "mention" module on each text editor as it finishes
 * rendering, so typing "@" opens a User picker backed by `search_link`. The
 * module is only attached once per editor (guarded on `modules.mention`), and the
 * search maps results to {id: user, value: full name} entries.
 */
frappe.ui.form.on("ControlTextEditor", {
	render_complete: function (frm) {
		var quill = this.quill;
		if (quill && !quill.options.modules.mention) {
			quill.options.modules.mention = {
				allowedChars: /^[A-Za-z\sÅÄÖåäö]*$/,
				mentionDenotationChars: ["@"],
				source: function (searchTerm, renderList) {
					frappe.call({
						method: "frappe.desk.search.search_link",
						args: {
							doctype: "User",
							txt: searchTerm,
						},
						callback: function (r) {
							var users = r.results.map(function (user) {
								return { id: user.value, value: user.description };
							});
							renderList(users, searchTerm);
						},
					});
				},
			};
		}
	},
});
