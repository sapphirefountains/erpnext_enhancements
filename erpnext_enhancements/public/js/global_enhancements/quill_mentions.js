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
