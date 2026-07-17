/**
 * @file Client-side controller for the custom "Project Dashboard" desk page.
 * @description
 * This is the page script for the Frappe Page `project-dashboard` (defined by
 * page/project_dashboard/project_dashboard.json). Frappe auto-loads it and calls
 * `frappe.pages["project-dashboard"].on_page_load` when the page is opened.
 *
 * Responsibilities:
 *  - Gate access: calls the whitelisted `check_permission`
 *    (page/project_dashboard/project_dashboard.py) and renders an "Access Denied"
 *    panel for users whose roles are not permitted. Page access — not per-record
 *    Project permission — is the intended gate (see the server docstring).
 *  - Header actions: "New Project" / "New Master Project" quick-create buttons.
 *  - Tabbed shell + routing: builds the tab bar (Dashboard, Priority Overview, Active
 *    Internal Projects, Completed Projects, Portfolio Gantt, Tasks View) and maps the sub-route
 *    (e.g. `project-dashboard/portfolio-gantt`) to a lazily `frappe.require`-loaded
 *    component class under `erpnext_enhancements.dashboard_components.*`. Each tab's
 *    actual rendering lives in a separate component file in
 *    public/js/project_enhancements/dashboard_components/ (not in this file).
 *  - Shared controls: global search box, dynamic field filters, and an auto-save
 *    handler that listens for the `dashboard_project_change` jQuery event and persists
 *    inline edits via the whitelisted `update_project_details`.
 *  - All server calls funnel through the `erpnext_enhancements.dashboard_api` helper,
 *    loaded first from public/js/.../dashboard_components/dashboard_api.js.
 *
 * Realtime: individual components subscribe to the "project_dashboard_updated"
 * realtime event (published by publish_realtime_update on Project/Task on_update)
 * to refresh themselves; this shell file handles navigation and shared state.
 */
/* global erpnext_enhancements */

frappe.pages["project-dashboard"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: "Projects Dashboard",
		single_column: true,
	});

	// Load the dashboard API utility first
	frappe.require("/assets/erpnext_enhancements/js/project_enhancements/dashboard_components/dashboard_api.js", () => {
		// Check permissions before rendering the main dashboard
		erpnext_enhancements.dashboard_api
			.call({
				method: "erpnext_enhancements.project_enhancements.page.project_dashboard.project_dashboard.check_permission",
			})
			.then((r) => {
				if (r.message) {
					initialize_dashboard(page);
				} else {
					page.set_title("Access Denied");
					$(page.body).html(`
                    <div class="container py-5">
                        <div class="alert alert-danger">
                            <h3><i class="fa fa-lock mr-2"></i>Access Denied</h3>
                            <p>You do not have the required permissions to view the Project Dashboard. Please contact your system administrator to request access.</p>
                        </div>
                    </div>
                `);
				}
			})
			.catch((err) => {
				console.error("Permission Check Error:", err);
				$(page.body).html(
					'<div class="alert alert-danger">Error checking permissions. Please try again.</div>'
				);
			});
	});

	function initialize_dashboard(page) {
		console.log("Loading Project Dashboard JS - Refactored");

		// --- Header actions: quick-create a Project or Master Project ---
		page.set_primary_action(__("New Project"), () => frappe.new_doc("Project"), "add");
		page.add_inner_button(__("New Master Project"), () => frappe.new_doc("Master Project"));

		// --- UI Container ---
		// Using standard Frappe layout classes
		const container = $('<div class="project-dashboard-container"></div>').appendTo(page.body);

		// --- Responsive State Handling ---
		const handleViewportMutation = frappe.utils.debounce(() => {
			const width = $(window).width();
			const contentContainer = container.find(".dashboard-content");

			if (width < 768) {
				// Mobile layout adjustments
				contentContainer.addClass("table-responsive");
				tabContainer
					.find(".nav-tabs")
					.addClass("flex-column nav-pills")
					.removeClass("nav-tabs");
				tabContainer.removeClass("border-bottom");
			} else {
				// Desktop layout adjustments
				contentContainer.removeClass("table-responsive");
				tabContainer
					.find(".nav-pills")
					.addClass("nav-tabs")
					.removeClass("flex-column nav-pills");
				tabContainer.addClass("border-bottom");
			}
		}, 250);

		$(window).on("resize", handleViewportMutation);

		// Standard Frappe Tab Navigation
		const tabContainer = $(`
            <div class="dashboard-tabs p-3 pb-0 border-bottom">
                <ul class="nav nav-tabs" role="tablist">
                    <li class="nav-item">
                        <a class="nav-link" href="javascript:void(0)" data-route="dashboard">Dashboard</a>
                    </li>
                    <li class="nav-item">
                        <a class="nav-link" href="javascript:void(0)" data-route="priority-overview">Priority Overview</a>
                    </li>
                    <li class="nav-item">
                        <a class="nav-link" href="javascript:void(0)" data-route="active-internal-projects">Active Internal Projects</a>
                    </li>
                    <li class="nav-item">
                        <a class="nav-link" href="javascript:void(0)" data-route="completed-projects">Completed Projects</a>
                    </li>
                    <li class="nav-item">
                        <a class="nav-link" href="javascript:void(0)" data-route="portfolio-gantt">Portfolio Gantt</a>
                    </li>
                    <li class="nav-item">
                        <a class="nav-link" href="javascript:void(0)" data-route="tasks-view">Tasks View</a>
                    </li>
                </ul>
            </div>
        `).appendTo(container);

		// Bind click events to tabs to update the route
		tabContainer.find(".nav-link").on("click", function (e) {
			e.preventDefault();
			const route = $(this).data("route");
			if (route) {
				frappe.set_route("project-dashboard", route);
			}
		});

		const controlsContainer = $(`
            <div class="dashboard-controls p-2 border-bottom" style="background: var(--subtle-fg);">
                <div class="row align-items-center">
                    <div class="col-md-8 mb-2 mb-md-0 d-flex align-items-center">
                        <div class="input-group input-group-sm mr-2" style="max-width: 300px;">
                            <input type="text" class="form-control" id="global-project-search" placeholder="Search projects...">
                        </div>
                        <button type="button" class="btn btn-sm btn-default" id="add-filter-btn">
                            <i class="fa fa-filter"></i> Add Filter
                        </button>
                        <div id="priority-overview-filters" style="display: none;" class="ml-2 btn-group btn-group-sm">
                            <button type="button" class="btn btn-default active" id="filter-company-priority">Company Priority</button>
                            <button type="button" class="btn btn-default" id="filter-value-stream">By Value Stream</button>
                        </div>
                    </div>
                    <div class="col-md-4 text-right">
                        <div id="global-pending-changes" style="display: none;">
                            <div class="btn-group btn-group-sm">
                                <button type="button" class="btn btn-success" id="save-global-changes">Save Changes</button>
                                <button type="button" class="btn btn-danger" id="discard-global-changes">Discard Changes</button>
                            </div>
                        </div>
                    </div>
                </div>
                <div id="active-filters-container" class="mt-2" style="display: none;">
                    <!-- Active filter badges will go here -->
                </div>
            </div>
        `).appendTo(container);

		const contentContainer = $('<div class="dashboard-content p-3" style="background: var(--card-bg);"></div>').appendTo(
			container
		);

		let currentComponent = null;

		// --- Route Handling & Component Dispatcher ---
		async function handleRouteChange() {
			const route = frappe.get_route();
			if (route[0] !== "project-dashboard") return;

			const moduleRoute =
				route[1] ||
				localStorage.getItem("project_dashboard_default_tab") ||
				"dashboard";

			// Save active tab preference
			localStorage.setItem("project_dashboard_default_tab", moduleRoute);
			const componentArgs = route.slice(2);

			// Update Tab Active State
			tabContainer.find(".nav-link").removeClass("active");
			tabContainer.find(`.nav-link[data-route="${moduleRoute}"]`).addClass("active");

			// Unmount current component (aborting any pending requests)
			if (currentComponent) {
				if (typeof currentComponent.unmount === "function") {
					currentComponent.unmount();
				}
				currentComponent = null;
			}

			// Show or hide Priority Overview specific controls
			if (moduleRoute === "priority-overview") {
				$("#priority-overview-filters").show();
				// Reset active state to default when returning to the tab
				$("#filter-company-priority").addClass("active").siblings().removeClass("active");
			} else {
				$("#priority-overview-filters").hide();
			}

			// Map route to Component Class & File
			let componentConfig;
			switch (moduleRoute) {
				case "dashboard":
					componentConfig = {
						file: "/assets/erpnext_enhancements/js/project_enhancements/dashboard_components/dashboard_view.js",
						className: "DashboardOverview",
					};
					break;
				case "active-internal-projects":
					componentConfig = {
						file: "/assets/erpnext_enhancements/js/project_enhancements/dashboard_components/active_internal_projects.js",
						className: "ActiveInternalProjects",
					};
					break;
				case "priority-overview":
					componentConfig = {
						file: "/assets/erpnext_enhancements/js/project_enhancements/dashboard_components/priority_overview.js",
						className: "PriorityOverview",
					};
					break;
				case "portfolio-gantt":
					componentConfig = {
						file: "/assets/erpnext_enhancements/js/project_enhancements/dashboard_components/portfolio_gantt.js",
						className: "PortfolioGantt",
					};
					break;
				case "tasks-view":
					componentConfig = {
						file: "/assets/erpnext_enhancements/js/project_enhancements/dashboard_components/tasks_view.js",
						className: "TasksView",
					};
					break;
				case "completed-projects":
					componentConfig = {
						file: "/assets/erpnext_enhancements/js/project_enhancements/dashboard_components/completed_projects.js",
						className: "CompletedProjects",
					};
					break;
				default:
					// Fallback to the Dashboard overview tab
					frappe.set_route("project-dashboard", "dashboard");
					return;
			}

			try {
				// Async Factory Pattern: Load file dynamically
				await new Promise((resolve) => {
					frappe.require(componentConfig.file, resolve);
				});

				// Instantiate the loaded component
				const ComponentClass =
					erpnext_enhancements.dashboard_components[componentConfig.className];
				if (ComponentClass) {
					currentComponent = new ComponentClass(contentContainer);
					// Pass any extra route args to render
					currentComponent.render(...componentArgs);
				} else {
					console.error(`Component class ${componentConfig.className} not found.`);
				}
			} catch (err) {
				console.error(`Error loading component for route ${moduleRoute}:`, err);
				contentContainer.html(
					'<div class="alert alert-danger">Error loading component module.</div>'
				);
			}
		}

		// --- Interactive State & Auto Saving ---
		$(document).on("dashboard_project_change", (e, data) => {
			erpnext_enhancements.dashboard_api
				.call({
					method: "erpnext_enhancements.project_enhancements.page.project_dashboard.project_dashboard.update_project_details",
					args: {
						project_name: data.project,
						field: data.field,
						value: data.value,
					},
				})
				.then((r) => {
					if (r.message && r.message.status === "success") {
						frappe.show_alert({
							message: "Change saved automatically",
							indicator: "green",
						});
					} else {
						frappe.show_alert({
							message: r.message ? r.message.message : "Error saving change.",
							indicator: "red",
						});
						handleRouteChange(); // Reload to revert
					}
				})
				.catch((err) => {
					frappe.show_alert({
						message: err.message || "Error saving change.",
						indicator: "red",
					});
					handleRouteChange(); // Reload to revert
				});
		});

		// Search Filtering Support
		let activeFilters = [];

		function applyFilters() {
			const searchTerm = $("#global-project-search").val().toLowerCase();
			const rows = contentContainer.find("table tbody tr");

			rows.each(function () {
				const row = $(this);
				let showRow = true;

				// 1. Text Search
				if (searchTerm) {
					const text = row.text().toLowerCase();
					if (text.indexOf(searchTerm) === -1) {
						showRow = false;
					}
				}

				// 2. Dynamic Filters
				if (showRow && activeFilters.length > 0) {
					for (let filter of activeFilters) {
						// Attempt to find the value in the row.
						// It could be in a select element or regular text.
						const selectEl = row.find(`select[data-field="${filter.fieldname}"]`);
						let rowValue = null;

						if (selectEl.length > 0) {
							rowValue = selectEl.val();
						} else {
							// If it's not an editable select, try to find a data attribute
							// For this to work fully for non-editable fields, the components
							// need to store data attributes on the row.
							rowValue = row.data(filter.fieldname);
						}

						// If we can't find it easily in DOM, we check if it was set via data attributes.
						if (rowValue !== undefined && rowValue !== null) {
							// Simple equality check (convert both to strings for comparison)
							if (
								String(rowValue).toLowerCase() !==
								String(filter.value).toLowerCase()
							) {
								showRow = false;
								break;
							}
						} else {
							// If the field isn't in data attributes, it might be undefined for this row.
							// If filter value is set, it means the filter condition isn't met.
							showRow = false;
							break;
						}
					}
				}

				if (showRow) {
					row.show();
				} else {
					row.hide();
				}
			});
		}

		$("#global-project-search").on("keyup", frappe.utils.debounce(applyFilters, 300));

		// Re-apply filters when route changes
		frappe.router.on("change", () => {
			setTimeout(applyFilters, 100); // Give component time to render
		});

		// Priority Overview specific filters
		$("#filter-company-priority").on("click", function () {
			$(this).addClass("active").siblings().removeClass("active");
			if (currentComponent && typeof currentComponent.set_view === "function") {
				currentComponent.set_view("company_priority");
			}
		});

		$("#filter-value-stream").on("click", function () {
			$(this).addClass("active").siblings().removeClass("active");
			if (currentComponent && typeof currentComponent.set_view === "function") {
				currentComponent.set_view("value_stream");
			}
		});

		// Dynamic Filter Add Logic
		$("#add-filter-btn").on("click", () => {
			frappe.model.with_doctype("Project", () => {
				const meta = frappe.get_meta("Project");
				// Filter out non-data fields for filtering
				const filterableFields = meta.fields.filter(
					(df) =>
						["Select", "Link", "Data", "Check"].includes(df.fieldtype) && !df.hidden
				);

				const d = new frappe.ui.Dialog({
					title: "Add Filter",
					fields: [
						{
							label: "Field",
							fieldname: "filter_field",
							fieldtype: "Select",
							options: filterableFields.map((df) => ({
								label: df.label,
								value: df.fieldname,
							})),
							reqd: 1,
							onchange: function () {
								const selectedFieldname = this.get_value();
								const fieldDef = filterableFields.find(
									(df) => df.fieldname === selectedFieldname
								);

								// Reset the value field based on selected type
								const valueField = d.get_field("filter_value");
								if (fieldDef) {
									valueField.df.fieldtype = fieldDef.fieldtype;
									valueField.df.options = fieldDef.options;
									valueField.refresh();
								}
							},
						},
						{
							label: "Value",
							fieldname: "filter_value",
							fieldtype: "Data",
							reqd: 1,
						},
					],
					primary_action_label: "Apply",
					primary_action: (values) => {
						const fieldDef = filterableFields.find(
							(df) => df.fieldname === values.filter_field
						);
						activeFilters.push({
							fieldname: values.filter_field,
							label: fieldDef ? fieldDef.label : values.filter_field,
							value: values.filter_value,
						});

						renderActiveFilters();
						applyFilters();
						d.hide();
					},
				});

				// Trigger initial onchange
				if (filterableFields.length > 0) {
					d.get_field("filter_field").set_value(filterableFields[0].fieldname);
				}

				d.show();
			});
		});

		function renderActiveFilters() {
			const container = $("#active-filters-container");
			if (activeFilters.length === 0) {
				container.hide();
				return;
			}

			container.empty().show();

			activeFilters.forEach((filter, index) => {
				const badge = $(`
                    <span class="badge badge-secondary mb-1 mr-1" style="font-size: 0.85em; font-weight: normal; padding: 0.4em 0.6em;">
                        ${frappe.utils.escape_html(
							filter.label
						)}: <strong>${frappe.utils.escape_html(String(filter.value))}</strong>
                        <a href="javascript:void(0)" class="text-white ml-2 remove-filter" data-index="${index}"><i class="fa fa-times"></i></a>
                    </span>
                `);
				container.append(badge);
			});

			container.find(".remove-filter").on("click", function () {
				const idx = $(this).data("index");
				activeFilters.splice(idx, 1);
				renderActiveFilters();
				applyFilters();
			});
		}

		// --- Event Delegation & Initialization ---
		frappe.router.on("change", handleRouteChange);

		// Initial render logic
		const currentRoute = frappe.get_route();
		if (currentRoute.length === 1 && currentRoute[0] === "project-dashboard") {
			// No sub-route, default to priority overview or local storage preference
			const defaultTab =
				localStorage.getItem("project_dashboard_default_tab") || "dashboard";
			frappe.set_route("project-dashboard", defaultTab);
		} else {
			// Trigger route logic for current route
			handleRouteChange();
		}

		// Initial responsive check
		handleViewportMutation();

		// Cleanup listener on page leave
		page.wrapper.on("hide", () => {
			frappe.router.off("change", handleRouteChange);
			$(window).off("resize", handleViewportMutation);
			if (currentComponent && typeof currentComponent.unmount === "function") {
				currentComponent.unmount();
			}
		});

		page.wrapper.on("show", () => {
			// Rebind when coming back
			frappe.router.on("change", handleRouteChange);
			handleRouteChange();
		});
	}
};
