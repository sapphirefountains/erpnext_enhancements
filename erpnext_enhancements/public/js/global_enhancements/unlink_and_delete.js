// v4 - Foolproof interceptor for Link Conflict errors in Frappe.
//
// Targets: every desk page (intercepts global delete errors).
// Loaded via: hooks.py `app_include_js` (global).
//
// When Frappe blocks a delete because the document "is linked with" others
// (LinkExistsError), the stock UI just shows an error. This script monkey-patches
// frappe.msgprint / frappe.show_alert / frappe.request.error to catch those link
// conflicts, parse out the offending doctype+name, and offer an "Unlink and
// Delete" dialog. It lists the blocking links (via delete_utils.get_blocking_links)
// and, on confirm, calls delete_utils.unlink_and_delete then navigates away or
// refreshes the list. Patches are applied on load, on app_ready, and re-checked
// every 2s (each guarded by a `_patched_v4` flag) since frappe may reassign these
// functions during SPA navigation.
(function () {
	let _unlink_dialog_active = false;

	const patch_msgprint = () => {
		if (!frappe.msgprint || frappe.msgprint._patched_v4) return;

		const _original_msgprint = frappe.msgprint;
		frappe.msgprint = function (args, ...rest) {
			let message = "";
			if (typeof args === "string") {
				message = args;
			} else if (args && args.message) {
				message = args.message;
			}

			if (message && (message.includes("is linked with") || message.includes("LinkExistsError"))) {
				_extract_and_show(message);
				return; 
			}

			return _original_msgprint.apply(this, [args, ...rest]);
		};
		frappe.msgprint._patched_v4 = true;
	};

	const patch_request_error = () => {
		if (!frappe.request || !frappe.request.error || frappe.request.error._patched_v4) return;

		const _original_request_error = frappe.request.error;
		frappe.request.error = function (request, r, opts) {
			if (r.status === 417 || (r.responseJSON && r.responseJSON._server_messages && r.responseJSON._server_messages.includes("LinkExistsError"))) {
				_extract_and_show(r);
				return;
			}
			return _original_request_error.apply(this, [request, r, opts]);
		};
		frappe.request.error._patched_v4 = true;
	};

	function _extract_and_show(source) {
		let message = "";
		if (typeof source === "string") {
			message = source;
		} else if (source.responseJSON && source.responseJSON._server_messages) {
			try {
				const msgs = JSON.parse(source.responseJSON._server_messages);
				message = msgs.map(m => JSON.parse(m).message || m).join("<br>");
			} catch (e) {
				message = "Link conflict detected.";
			}
		}

		// Try to parse doctype and name
		let doctype, name;
		const temp_div = document.createElement("div");
		temp_div.innerHTML = message;
		const links = temp_div.querySelectorAll("a");
		
		if (links.length >= 2) {
			const target_link = links[0];
			const href = target_link.getAttribute("href");
			if (href) {
				const parts = href.split("/");
				doctype = parts[parts.length - 2];
				name = parts[parts.length - 1];
			}
		}

		if (!doctype || !name) {
			const route = frappe.get_route();
			if (route && route[0] === "Form") {
				doctype = route[1];
				name = route[2];
			}
		}

		if (doctype && name) {
			_show_unlink_dialog(doctype, name, message);
		} else {
			if (typeof source === "string") {
				alert(message);
			}
		}
	}

	const patch_show_alert = () => {
		if (!frappe.show_alert || frappe.show_alert._patched_v4) return;
		const _original_show_alert = frappe.show_alert;
		frappe.show_alert = function (args, ...rest) {
			let message = (typeof args === "string") ? args : (args.message || "");
			if (message && message.includes("is linked with")) {
				_extract_and_show(message);
				return;
			}
			return _original_show_alert.apply(this, [args, ...rest]);
		};
		frappe.show_alert._patched_v4 = true;
	};

	function _show_unlink_dialog(doctype, name, original_error_msg) {
		if (_unlink_dialog_active) return;
		_unlink_dialog_active = true;

		// Fetch detailed links
		frappe.call({
			method: "erpnext_enhancements.delete_utils.get_blocking_links",
			args: { doctype, name },
			callback: function (res) {
				const links = res.message || [];
				let links_html = "";

				if (links.length > 0) {
					links_html = `<div class="text-muted small" style="margin-top:10px;">
						<b>${__("The following links will be removed:")}</b>
						<ul style="padding-left:20px; margin-top:5px; max-height: 150px; overflow-y: auto;">
							${links
								.map(
									(l) =>
										`<li>${l.doctype}: <b>${l.name}</b> ${
											l.is_child ? `(Row ${l.idx})` : ""
										}</li>`
								)
								.join("")}
						</ul>
					</div>`;
				}

				const d = new frappe.ui.Dialog({
					title: __("Link Conflict Detected"),
					fields: [
						{
							fieldtype: "HTML",
							options: `
								<div class="alert alert-danger" style="margin-bottom:10px; font-size: 0.9em;">
									${original_error_msg}
								</div>
								<p><b>${__("Would you like to unlink these documents and then continue deleting?")}</b></p>
								${links_html}
							`,
						},
					],
					primary_action_label: __("Unlink and Delete"),
					primary_action() {
						if (document.activeElement) {
							document.activeElement.blur();
						}
						d.hide();
						frappe.call({
							method: "erpnext_enhancements.delete_utils.unlink_and_delete",
							args: { doctype, name },
							freeze: true,
							freeze_message: __("Unlinking and deleting…"),
							callback(res) {
								if (res.message && res.message.success) {
									frappe.show_alert({ message: __("{0} deleted.", [name]), indicator: "green" });
									frappe.model.clear_doc(doctype, name);
									
									const route = frappe.get_route();
									if (route[0] === "Form" && route[1] === doctype && route[2] === name) {
										window.history.back();
									} else if (window.cur_list && cur_list.doctype === doctype) {
										cur_list.refresh();
									}
								}
							},
						});
					},
					secondary_action_label: __("Abort"),
					secondary_action() {
						if (document.activeElement) {
							document.activeElement.blur();
						}
						d.hide();
					},
				});

				d.onhide = () => { _unlink_dialog_active = false; };
				d.show();
			},
		});
	}

	// Apply patches
	const apply_all = () => {
		if (window.frappe) {
			patch_msgprint();
			patch_show_alert();
			if (frappe.request) patch_request_error();
		}
	};

	apply_all();
	$(document).on("app_ready", apply_all);
	setInterval(apply_all, 2000);
})();
