/**
 * @file Custom client-side script for the Project list view.
 * @description This script enhances the Project list view by setting a default
 * grouping rule. It fetches the grouping configuration from the server
 * and applies it when the list view is initialized.
 */

frappe.listview_settings["Project"] = {
	/**
	 * Configures the Project list view on initialization.
	 *
	 * This function is automatically called by the Frappe framework when the
	 * Project list view is loaded. It makes a server call to a whitelisted
	 * Python function (`get_project_grouping_option`) to fetch the default
	 * grouping preference and applies it to the list view settings.
	 *
	 * @param {object} listview - The list view instance, which allows for
	 * programmatic customization of its appearance and behavior.
	 */
	setup: function (listview) {
		// Browser-side debug message to indicate the script is running.
		console.log("Browser DEBUG: Calling server script for group_by settings.");

		// Asynchronous server call to the backend Python method.
		frappe.call({
			// The method path follows the format:
			// app_name.path.to.module.function_name
			method: "erpnext_enhancements.project_enhancements.doctype.project.project.get_project_grouping_option",

			/**
			 * Handles the response from the server.
			 * @param {object} r - The response object from the server. The actual
			 * return value of the Python function is in `r.message`.
			 */
			callback: function (r) {
				// Log the full server response for debugging.
				console.log("Browser DEBUG: Received response from server:", r);

				// Check if the response contains the expected 'group_by' setting.
				if (r.message && r.message.group_by) {
					console.log("Browser DEBUG: Applying group_by setting:", r.message.group_by);

					// Set the group_by property on the listview instance.
					listview.group_by = r.message.group_by;

					// Refresh the listview to apply the new grouping.
					listview.refresh();
				} else {
					console.error(
						"Browser DEBUG: Invalid response from server or missing group_by key in message."
					);
				}
			},
		});
	},
};
