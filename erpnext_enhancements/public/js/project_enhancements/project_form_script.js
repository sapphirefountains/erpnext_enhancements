/* global erpnext_enhancements */

const EXTERNAL_PROJECT_TYPES = ["Service", "Rent", "Build", "Design"];

function toggle_master_project(frm) {
	const is_external = EXTERNAL_PROJECT_TYPES.includes(frm.doc.project_type);
	frm.set_df_property("custom_master_project", "hidden", is_external ? 1 : 0);
}

frappe.ui.form.on("Project", {
	project_type: function (frm) {
		toggle_master_project(frm);
	},
});

frappe.ui.form.on("Project", {
	refresh: function (frm) {
		toggle_master_project(frm);

		if (frm.is_new()) {
			return;
		}

		// =========================================================================
		// 1. STATE CLEANUP FOR SPA NAVIGATION
		// =========================================================================
		console.log(
			`Project form refreshed: ${frm.doc.name}. Cleaning up old tree state to prevent detached nodes.`
		);
		if (frm.task_tree_instance) {
			if (frm.task_tree_instance.sortableInstances) {
				frm.task_tree_instance.sortableInstances.forEach((instance) => instance.destroy());
			}
			frm.task_tree_instance = null;
		}
		if (frm.get_field("custom_tasks_html") && frm.get_field("custom_tasks_html").$wrapper) {
			frm.get_field("custom_tasks_html").$wrapper.empty();
		}
		frm._current_task_tree_project = frm.doc.name;

		// =========================================================================
		// 2. ORIGINAL REPO LOGIC: Move Activity and Connections sections
		// =========================================================================
		const formTabs = frm.$wrapper.find(".form-tabs");
		const detailsTab = formTabs.find('.nav-item[data-label="Details"]');
		const activitySection = frm.$wrapper.find('[data-label="Activity"]');
		const activitySectionContainer = activitySection.closest(".frappe-control");
		const connectionsSection = frm.$wrapper.find('[data-label="Connections"]');

		if (detailsTab.length && activitySection.length) {
			detailsTab
				.closest(".form-layout")
				.find('.tab-content .tab-pane[data-label="Details"]')
				.append(activitySectionContainer);
			if (connectionsSection.length) {
				detailsTab
					.closest(".form-layout")
					.find('.tab-content .tab-pane[data-label="Details"]')
					.append(connectionsSection.closest(".frappe-control"));
			}
		}

		// =========================================================================
		// 3. TREE VIEW RENDERER
		// =========================================================================
		// Note: frappe.has_permission is a server-side (Python) API and does not
		// exist on the client. Use frappe.model.can_read for the desk-side check.
		if (frappe.model.can_read("Task")) {
			const wrapperField = frm.get_field("custom_tasks_html");

			if (wrapperField) {
				// Only bind the override once to avoid memory leaks on multiple saves
				if (!wrapperField.__custom_tree_bound) {
					const original_refresh = wrapperField.refresh;

					wrapperField.refresh = function () {
						// Let Frappe do its native HTML field setup first
						if (original_refresh) {
							original_refresh.call(this);
						}

						// By the time this runs, the tab has been clicked and this.$wrapper is guaranteed to exist.
						// Guard on our own injected marker (.task-tree-container) rather than a raw
						// children() count: a Frappe HTML control's $wrapper already contains internal
						// label/input structure after original_refresh, so children().length is never 0
						// and the tree would otherwise never render.
						if (this.$wrapper && this.$wrapper.find(".task-tree-container").length === 0) {
							const docName = frm.doc.name;

							frappe
								.require("/assets/erpnext_enhancements/js/task_tree_manager.js")
								.then(() => {
									if (
										window.project_enhancements &&
										erpnext_enhancements.TaskTreeManager
									) {
										console.log(`Rendering Task Tree for Project: ${docName}`);

										if (frm.task_tree_instance) {
											if (frm.task_tree_instance.sortableInstances) {
												frm.task_tree_instance.sortableInstances.forEach(
													(instance) => instance.destroy()
												);
											}
											frm.task_tree_instance = null;
										}

										this.$wrapper
											.empty()
											.html('<div class="task-tree-container"></div>');

										frm.task_tree_instance =
											new erpnext_enhancements.TaskTreeManager({
												wrapper: this.$wrapper.find(".task-tree-container"),
												projectName: docName,
											});
									}
								});
						}
					};

					wrapperField.__custom_tree_bound = true;
				}

				// Eagerly render on page load. The field's $wrapper may not be built
				// yet at the moment this refresh handler runs, so poll for it instead of
				// giving up after a single check. This renders the tree automatically
				// without waiting for the user to click the Scope tab. The tree is pure
				// DOM (no size dependency), so it renders fine while its tab is hidden.
				const pollProject = frm.doc.name;
				let treeRenderAttempts = 0;
				let scopeTabBuildForced = false;

				// Fallback for Frappe builds that lazily render a tab's fields only when
				// the tab is first shown: briefly activate the Scope tab to force its
				// content to build, then restore the originally active tab. The
				// shown.bs.tab handler (section 4.5) then renders the tree.
				const forceScopeTabBuild = () => {
					const $scopeLink = frm.$wrapper
						.find(
							'.form-tabs .nav-item[data-label="Scope"] a.nav-link, ' +
								'.form-tabs .nav-item[data-fieldname="custom_scope"] a.nav-link'
						)
						.first();
					const $activeLink = frm.$wrapper.find(".form-tabs .nav-link.active").first();
					if (!$scopeLink.length || !$scopeLink.tab) {
						return;
					}
					// Already on the Scope tab: just show it (no restore needed).
					if (!$activeLink.length || $activeLink[0] === $scopeLink[0]) {
						$scopeLink.tab("show");
						return;
					}
					// Restore the user's tab once Scope has finished building/showing.
					$scopeLink.one("shown.bs.tab", () => $activeLink.tab("show"));
					$scopeLink.tab("show");
				};

				const tryRenderTaskTree = () => {
					// Abort if the form navigated to a different project (SPA navigation)
					if (frm.doc.name !== pollProject) {
						return;
					}
					if (wrapperField.$wrapper) {
						wrapperField.refresh();
						return;
					}
					// Wrapper still not built after a short grace period: force the
					// Scope tab to build its content (handles lazy tab rendering).
					if (treeRenderAttempts === 15 && !scopeTabBuildForced) {
						scopeTabBuildForced = true;
						forceScopeTabBuild();
					}
					if (treeRenderAttempts++ < 60) {
						setTimeout(tryRenderTaskTree, 100);
					}
				};
				tryRenderTaskTree();
			}
		}

		// =========================================================================
		// 4. ORIGINAL REPO LOGIC: Deep linking logic from Dashboard
		// =========================================================================
		const checkAndSwitchToScopeTab = () => {
			if (window.location.hash === "#custom_scope") {
				setTimeout(() => {
					const scopeTab = formTabs.find(
						'.nav-item[data-label="Scope"], .nav-item[data-fieldname="custom_scope"]'
					);
					if (scopeTab.length) {
						const tabLink = scopeTab.find("a.nav-link");
						if (tabLink.length) {
							tabLink.click();
						} else {
							scopeTab.click();
						}
					}
				}, 300);
			}
		};

		checkAndSwitchToScopeTab();
		$(window).on("hashchange", checkAndSwitchToScopeTab);

		// =========================================================================
		// 4.5. ROBUST TAB RENDERING (Tree & Gantt)
		// =========================================================================
		// Use Bootstrap's native event which fires AFTER the tab is fully visible
		// This guarantees that elements have height/width for charts and trees to calculate correctly.
		frm.$wrapper.on('shown.bs.tab', 'a[data-toggle="tab"]', function (e) {
			const tabLabel = $(e.target).closest('.nav-item').attr('data-label') || '';
			const tabFieldname = $(e.target).closest('.nav-item').attr('data-fieldname') || '';

			if (tabLabel === "Scope" || tabFieldname === "custom_scope") {
				setTimeout(() => {
					const treeField = frm.get_field("custom_tasks_html");
					if (treeField && treeField.$wrapper) {
						console.log("Scope tab visible - refreshing Tree View.");
						treeField.refresh();
					}
				}, 150);
			} else if (tabLabel === "Schedule" || tabFieldname === "custom_schedule") {
				setTimeout(() => {
					const ganttField = frm.get_field("custom_gantt_chart_html");
					if (ganttField && ganttField.$wrapper) {
						console.log("Schedule tab visible - refreshing Gantt Chart.");
						ganttField.refresh();
					}
				}, 150);
			}
		});

		// =========================================================================
		// 5. HIDE STANDARD VIEW BUTTON
		// =========================================================================
		const styleId = "hide-standard-view-btn-style";
		if (!document.getElementById(styleId)) {
			const styleEl = document.createElement("style");
			styleEl.id = styleId;
			styleEl.innerHTML = `
                .inner-group-button[data-label="View"],
                .custom-btn-group[data-label="View"],
                .inner-group-button[data-label="View Tasks"],
                .custom-btn-group[data-label="View Tasks"] {
                    display: none !important;
                }
            `;
			document.head.appendChild(styleEl);
		}

		setTimeout(() => {
			if (frm.page && frm.page.clear_custom_button) {
				try {
					frm.page.clear_custom_button('View');
				} catch (e) {
					console.warn("Failed to clear standard 'View' button via API, CSS fallback active.", e);
				}
			}
		}, 100);
	},
});
