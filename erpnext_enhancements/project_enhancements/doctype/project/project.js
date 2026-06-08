frappe.ui.form.on("Project", {
	refresh: function (frm) {
		console.log("Project form refreshed - initializing Gantt check");
		
		const wrapperField = frm.get_field("custom_gantt_chart_html");

		// Global Health Indicator
		if (wrapperField && !frm.is_new()) {
			if (!wrapperField.__health_bound) {
				wrapperField.render_health_indicator = function(frm) {
					frappe.call({
						method: "erpnext_enhancements.project_enhancements.page.project_dashboard.project_dashboard.get_project_health_metrics",
						args: { project_name: frm.doc.name },
						callback: (r) => {
							if (r.message && r.message.total_tasks > 0) {
								const data = r.message;
								const schedule_color = data.schedule_health > 80 ? 'text-success' : (data.schedule_health > 50 ? 'text-warning' : 'text-danger');
								const html = `<div class="project-health-dashboard d-flex align-items-center p-3 mb-3 bg-white border rounded shadow-sm"><div class="health-metric mr-4 text-center" style="min-width: 100px;"><div class="h3 mb-0 ${schedule_color}">${data.schedule_health}%</div><div class="small text-muted text-uppercase font-weight-bold">Schedule Health</div></div><div class="health-metric mr-4 border-left pl-4"><div class="d-flex align-items-baseline"><span class="h4 mb-0 mr-2">${data.overdue_count}</span><span class="small text-muted">Overdue Tasks</span></div>${data.high_priority_overdue > 0 ? `<div class="small text-danger"><i class="fa fa-exclamation-triangle"></i> ${data.high_priority_overdue} High Priority Overdue</div>` : ''}</div><div class="health-metric mr-4 border-left pl-4 flex-grow-1"><div class="small d-flex justify-content-between mb-1"><span class="text-muted text-uppercase font-weight-bold">Overall Progress</span><span class="font-weight-bold">${data.overall_progress}%</span></div><div class="progress" style="height: 10px;"><div class="progress-bar bg-success" role="progressbar" style="width: ${data.overall_progress}%"></div></div></div><div class="health-metric border-left pl-4 text-center"><div class="h4 mb-0 text-primary">${data.completed_count}/${data.total_tasks}</div><div class="small text-muted">Tasks Done</div></div></div>`;
								const $container = frm.$wrapper.find('.form-body');
								$container.find('.project-health-dashboard').remove();
								$container.prepend(html);
							}
						}
					});
				};
				wrapperField.__health_bound = true;
				frappe.realtime.on("project_dashboard_updated", (data) => {
					if (data.project === frm.doc.name) {
						wrapperField.render_health_indicator(frm);
						// Realtime updates are incremental (e.g. a successor date shift
						// or a new dependency). Preserve the user's scroll position
						// rather than snapping back to today on this background refresh.
						if (wrapperField.__custom_gantt_bound) {
							wrapperField.__preserve_scroll = true;
							wrapperField.refresh();
						}
					}
				});
			}
			wrapperField.render_health_indicator(frm);
		}

		if (!$("#custom-gantt-styles").length) {
			$("<style id='custom-gantt-styles'>").html(`.gantt .bar-overdue .bar { fill: #e74c3c !important; } .gantt .bar-overdue .bar-progress { fill: #c0392b !important; } .gantt .bar-milestone .bar { fill: #f1c40f !important; clip-path: polygon(50% 0%, 100% 50%, 50% 100%, 0% 50%); } .gantt .bar-milestone .bar-progress { display: none !important; } .gantt .baseline-bar { fill: #d1d8dd; opacity: 0.4; pointer-events: none; } .gantt .bar-wrapper.highlight .bar { stroke: #2980b9; stroke-width: 3 !important; } .heatmap-table { width: 100%; border-collapse: collapse; font-size: 11px; } .heatmap-table th, .heatmap-table td { border: 1px solid #eee; padding: 4px; text-align: center; } .heatmap-table .user-cell { text-align: left; background: #f9f9f9; font-weight: 500; min-width: 120px; } .workload-low { background-color: #d4edda !important; color: #155724; } .workload-med { background-color: #fff3cd !important; color: #856404; } .workload-high { background-color: #f8d7da !important; color: #721c24; } .heatmap-table td:not(.user-cell) { cursor: pointer; } .heatmap-table td:not(.user-cell):hover { filter: brightness(0.9); } .custom-gantt-popup { background: #fff; border-radius: 4px; padding: 12px; box-shadow: 0 2px 10px rgba(0,0,0,0.15); font-size: 13px; color: #333; min-width: 200px; border: 1px solid #e0e0e0; } .custom-gantt-popup h5 { margin: 0 0 8px; font-weight: 600; font-size: 14px; } .custom-gantt-popup p { margin: 0 0 4px; } .custom-gantt-popup .popup-label { font-weight: bold; color: #555; }`).appendTo("head");
		}

		if (wrapperField) {
			if (!wrapperField.__custom_gantt_bound) {
				const original_refresh = wrapperField.refresh;
				wrapperField.refresh = function () {
					if (original_refresh) original_refresh.call(this);
					if (this.$wrapper) {
						const gantt_wrapper = this.$wrapper;
						// When a refresh is triggered by an in-place edit (e.g. dragging a
						// task bar to change its dates), keep the user's current scroll
						// position instead of jumping back to today. The flag is set by the
						// handler that triggered the refresh and consumed here.
						const preserveScroll = wrapperField.__preserve_scroll === true;
						wrapperField.__preserve_scroll = false;
						let savedScrollLeft = null, savedScrollTop = null;
						if (preserveScroll) {
							const existing_gc = gantt_wrapper.find(".gantt-container")[0];
							if (existing_gc) {
								savedScrollLeft = existing_gc.scrollLeft;
								savedScrollTop = existing_gc.scrollTop;
							}
						}
						if (gantt_wrapper.find(".gantt-chart-container").length === 0) {
							const wrapper_top = gantt_wrapper[0].getBoundingClientRect().top;
							const gantt_height = Math.max(300, Math.floor((window.innerHeight - wrapper_top) * 0.75));
							gantt_wrapper.css({ height: gantt_height + "px", display: "flex", "flex-direction": "column", border: "1px solid #d1d8dd", "border-radius": "4px", "background-color": "#f8f9fa", "margin-bottom": "20px" });
							gantt_wrapper.empty().html(`<div class="gantt-toolbar d-flex justify-content-between p-2 bg-light border-bottom"><div class="export-actions"><button type="button" class="btn btn-default btn-sm btn-export-gantt"><i class="fa fa-camera mr-1"></i> Export PNG</button></div><div class="d-flex align-items-center"><div class="btn-group btn-group-sm zoom-mode-group mr-2"><button type="button" class="btn btn-default btn-zoom-out" title="Zoom out (show more)"><i class="fa fa-search-minus"></i></button><button type="button" class="btn btn-default btn-zoom-in" title="Zoom in (show detail)"><i class="fa fa-search-plus"></i></button></div><div class="btn-group btn-group-sm view-mode-group"><button type="button" class="btn btn-default" data-view="Quarter Day">Quarter Day</button><button type="button" class="btn btn-default" data-view="Half Day">Half Day</button><button type="button" class="btn btn-primary active" data-view="Day">Day</button><button type="button" class="btn btn-default" data-view="Week">Week</button><button type="button" class="btn btn-default" data-view="Month">Month</button></div></div></div><div class="gantt-chart-container" style="flex-grow: 1; overflow: hidden;"><p class="text-muted">Initializing Gantt Chart...</p></div><div class="resource-heatmap-container border-top" style="height: 150px; display: none;"><div class="heatmap-header p-2 bg-light d-flex justify-content-between"><span class="small font-weight-bold text-muted">RESOURCE ALLOCATION (HRS/DAY)</span><div class="heatmap-legend d-flex small align-items-center"><span class="mr-2"><i class="fa fa-square text-success"></i> < 6</span><span class="mr-2"><i class="fa fa-square text-warning"></i> 6-9</span><span><i class="fa fa-square text-danger"></i> > 9</span></div></div><div class="heatmap-body" style="overflow: auto; height: 110px;"></div></div>`);
						}
						const chart_container = gantt_wrapper.find(".gantt-chart-container");
						const heatmap_container = gantt_wrapper.find(".resource-heatmap-container");
						chart_container.html('<p class="text-muted">Fetching task data...</p>');
						frappe.call({
							method: "erpnext_enhancements.project_enhancements.page.project_dashboard.project_dashboard.get_gantt_tasks_for_project",
							args: { project_name: frm.doc.name },
							callback: (r) => {
								if (r.message && !r.message.error) {
									const tasks = r.message.map(t => {
										if (t.is_milestone) {
											let classes = (t.custom_class || "").split(" ").filter(c => c.trim().length > 0);
											if (!classes.includes("bar-milestone")) {
												classes.push("bar-milestone");
											}
											t.custom_class = classes.join(" ");
										}
										return t;
									});
									if (tasks.length === 0) { chart_container.html('<p class="text-muted text-center p-4">No tasks found for this project.</p>'); return; }
									wrapperField.render_heatmap(frm, heatmap_container);
									let clickTimer = null;
									// Zoom level persists across in-place refreshes within the session
									// (e.g. dragging a bar) but resets to the default on a fresh page load.
									const ZOOM = erpnext_enhancements.gantt_zoom;
									if (typeof wrapperField.__zoom_index !== "number") {
										wrapperField.__zoom_index = ZOOM.DEFAULT_INDEX.Day;
									}
									const zoom_level = ZOOM.level(wrapperField.__zoom_index);
									// Auto-scroll target: open the timeline on the FIRST task's start
									// date so the earliest scheduled work is immediately visible. The
									// frappe-gantt library accepts a "YYYY-MM-DD" string for scroll_to
									// and positions the viewport at that date. Task "start" values are
									// "YYYY-MM-DD" strings, so a lexicographic min is the earliest date.
									// Falls back to "start" (gantt's earliest position) when no task has
									// a date.
									const first_task_start = tasks.reduce((min, t) => (t.start && (!min || t.start < min) ? t.start : min), null);
									const scroll_target = first_task_start || "start";
									const options = {
										// On a preserve refresh, suppress the library's auto-scroll
										// so our manual restore (below) is the only thing that moves the
										// viewport. On a normal render, scroll to the first task's start
										// date (or gantt's earliest position when no task has a date).
										view_mode: zoom_level.view_mode, column_width: zoom_level.column_width, scroll_to: preserveScroll ? null : scroll_target,
										custom_popup_html: (task) => { let b_info = task.baseline_start ? `<p><span class="popup-label">Baseline:</span> ${moment(task.baseline_start).format('MMM D')} - ${moment(task.baseline_end).format('MMM D')}</p>` : ""; return `<div class="custom-gantt-popup"><h5>${task.name} ${task.is_milestone ? '<span class="badge badge-warning">Milestone</span>' : ''}</h5><p><span class="popup-label">Assignee:</span> ${task.assigned_to || 'Unassigned'}</p><p><span class="popup-label">Status:</span> ${task.status || 'N/A'}</p><p><span class="popup-label">Start:</span> ${moment(task.start).format('MMM D, YYYY')}</p><p><span class="popup-label">End:</span> ${moment(task.end).format('MMM D, YYYY')}</p>${b_info}<p><span class="popup-label">Progress:</span> ${task.progress}%</p><p style="font-size: 11px; margin-top: 8px; color: #777;"><em>Double-click bar to open task</em></p></div>`; },
										on_click: (task) => { if (clickTimer) { clearTimeout(clickTimer); clickTimer = null; frappe.set_route("Form", "Task", task.id); } else { clickTimer = setTimeout(() => { clickTimer = null; }, 250); } },
										on_date_change: (task, start, end) => { frappe.call({ method: "erpnext_enhancements.project_enhancements.page.project_dashboard.project_dashboard.update_task_dates_from_gantt", args: { task_name: task.id, start_date: moment(start).format("YYYY-MM-DD"), end_date: moment(end).format("YYYY-MM-DD") }, callback: (res) => { if (res.message && res.message.status === "success") { wrapperField.__preserve_scroll = true; wrapperField.refresh(); wrapperField.render_health_indicator(frm); } } }); },
										on_progress_change: (task, progress) => { frappe.call({ method: "erpnext_enhancements.project_enhancements.page.project_dashboard.project_dashboard.update_task_progress_from_gantt", args: { task_name: task.id, progress: parseInt(progress) }, callback: () => wrapperField.render_health_indicator(frm) }); },
									};
									chart_container.empty();
									if (typeof Gantt === "undefined") { chart_container.html('<p class="text-danger">Gantt library not loaded. Please refresh the page.</p>'); return; }
									try {
										const gantt = new Gantt(chart_container[0], tasks, options);
										// Toolbar sync: highlight the view-mode button matching the current
										// zoom level and disable the +/- buttons at the ends of the ladder.
										const sync_zoom_toolbar = () => {
											const lvl = ZOOM.level(wrapperField.__zoom_index);
											gantt_wrapper.find('.view-mode-group button').removeClass('active btn-primary').addClass('btn-default');
											gantt_wrapper.find('.view-mode-group button[data-view="' + lvl.view_mode + '"]').addClass('active btn-primary').removeClass('btn-default');
											gantt_wrapper.find('.btn-zoom-out').prop('disabled', ZOOM.is_min(wrapperField.__zoom_index));
											gantt_wrapper.find('.btn-zoom-in').prop('disabled', ZOOM.is_max(wrapperField.__zoom_index));
										};
										const apply_zoom = (index) => {
											wrapperField.__zoom_index = ZOOM.apply(gantt, index);
											sync_zoom_toolbar();
											setTimeout(setup_dependency_linking, 100);
										};
										// Discrete view-mode buttons jump to that mode's base zoom level so the
										// +/- buttons stay coherent afterwards (and use the correct column width).
										gantt_wrapper.find('.view-mode-group button').off('click').on('click', function() {
											const base = ZOOM.BASE_INDEX[$(this).data('view')];
											apply_zoom(typeof base === "number" ? base : wrapperField.__zoom_index);
										});
										gantt_wrapper.find('.btn-zoom-in').off('click').on('click', () => apply_zoom(wrapperField.__zoom_index + 1));
										gantt_wrapper.find('.btn-zoom-out').off('click').on('click', () => apply_zoom(wrapperField.__zoom_index - 1));
										sync_zoom_toolbar();
										const g_cont = gantt_wrapper.find(".gantt-container");
										g_cont.css({ "overflow-x": "scroll", "overflow-y": "auto", "max-height": "100%", "position": "relative" });

										// --- Dependency linking ---
										// Each task bar gets an always-visible round "link" handle at its
										// right edge. Press it and drag: a translucent ghost of the bar plus
										// a dashed connector follow the cursor. Release over another task bar
										// to make that task depend on the source (predecessor -> successor),
										// which renders as an arrow after the refresh. Bar-body dragging is
										// reserved for date changes, so linking uses this dedicated handle.
										const SVGNS = "http://www.w3.org/2000/svg";
										if (!$("#gantt-dep-link-styles").length) {
											$("<style id='gantt-dep-link-styles'>").html(`
												.gantt-link-handle { fill: #2563eb; stroke: #fff; stroke-width: 1.5; cursor: crosshair; opacity: 0.85; transition: opacity 0.15s, r 0.1s; }
												.gantt-link-handle:hover { opacity: 1; }
												.gantt .bar-wrapper:hover .gantt-link-handle { opacity: 1; }
												.gantt-link-line { stroke: #1d4ed8; stroke-width: 2.5; stroke-dasharray: 5 3; pointer-events: none; }
												.gantt-link-ghost { fill: #2563eb; opacity: 0.3; pointer-events: none; }
												.gantt .bar-wrapper.link-target-hover .bar { stroke: #1d4ed8 !important; stroke-width: 3px !important; }
											`).appendTo("head");
										}
										const setup_dependency_linking = () => {
											const svg = chart_container.find("svg").get(0);
											if (!svg) return;
											chart_container.find(".gantt-link-handle").remove();
											chart_container.find(".bar-wrapper").each(function () {
												const taskId = $(this).attr("data-id");
												const barEl = this.querySelector(".bar");
												if (!taskId || !barEl) return;
												const bbox = barEl.getBBox();
												const handle = document.createElementNS(SVGNS, "circle");
												handle.setAttribute("class", "gantt-link-handle");
												handle.setAttribute("cx", bbox.x + bbox.width);
												handle.setAttribute("cy", bbox.y + bbox.height / 2);
												handle.setAttribute("r", 5);
												handle.setAttribute("data-source", taskId);
												handle.setAttribute("data-h", bbox.height);
												this.appendChild(handle);
											});

											let linking = null;
											const clientToSvg = (clientX, clientY) => {
												const ctm = svg.getScreenCTM();
												if (!ctm) return { x: 0, y: 0 };
												const pt = svg.createSVGPoint();
												pt.x = clientX; pt.y = clientY;
												const p = pt.matrixTransform(ctm.inverse());
												return { x: p.x, y: p.y };
											};
											const clearLinking = () => {
												if (!linking) return;
												if (linking.line) linking.line.remove();
												if (linking.ghost) linking.ghost.remove();
												$(document).off("mousemove.ganttlink mouseup.ganttlink keydown.ganttlink");
												chart_container.find(".bar-wrapper").removeClass("link-target-hover");
												linking = null;
											};
											const onMouseMove = (e) => {
												if (!linking) return;
												const p = clientToSvg(e.clientX, e.clientY);
												linking.line.setAttribute("x2", p.x);
												linking.line.setAttribute("y2", p.y);
												// Ghost block follows the cursor.
												linking.ghost.setAttribute("x", p.x + 6);
												linking.ghost.setAttribute("y", p.y - linking.ghostH / 2);
												const el = document.elementFromPoint(e.clientX, e.clientY);
												const $tw = el ? $(el).closest(".bar-wrapper") : $();
												chart_container.find(".bar-wrapper").removeClass("link-target-hover");
												if ($tw.length && $tw.attr("data-id") !== linking.sourceId) $tw.addClass("link-target-hover");
											};
											const onMouseUp = (e) => {
												if (!linking) return;
												const sourceId = linking.sourceId;
												const el = document.elementFromPoint(e.clientX, e.clientY);
												const $tw = el ? $(el).closest(".bar-wrapper") : $();
												const targetId = $tw.attr("data-id");
												clearLinking();
												if (!targetId || targetId === sourceId) return;
												frappe.call({
													method: "erpnext_enhancements.project_enhancements.page.project_dashboard.project_dashboard.add_task_dependency",
													args: { task_name: targetId, depends_on_task: sourceId },
													callback: (r) => {
														if (r.message && r.message.status === "success") {
															frappe.show_alert({ message: __("Dependency added"), indicator: "green" });
															wrapperField.__preserve_scroll = true;
															wrapperField.refresh();
														} else {
															frappe.show_alert({ message: __((r.message && r.message.message) || "Failed to add dependency"), indicator: "red" });
														}
													},
												});
											};
											chart_container.off("mousedown.ganttlink", ".gantt-link-handle");
											chart_container.on("mousedown.ganttlink", ".gantt-link-handle", function (e) {
												e.preventDefault();
												e.stopPropagation();
												const sourceId = $(this).attr("data-source");
												const cx = parseFloat(this.getAttribute("cx"));
												const cy = parseFloat(this.getAttribute("cy"));
												const ghostH = parseFloat(this.getAttribute("data-h")) || 18;
												const line = document.createElementNS(SVGNS, "line");
												line.setAttribute("class", "gantt-link-line");
												line.setAttribute("x1", cx); line.setAttribute("y1", cy);
												line.setAttribute("x2", cx); line.setAttribute("y2", cy);
												const ghost = document.createElementNS(SVGNS, "rect");
												ghost.setAttribute("class", "gantt-link-ghost");
												ghost.setAttribute("width", 60);
												ghost.setAttribute("height", ghostH);
												ghost.setAttribute("rx", 3);
												ghost.setAttribute("x", cx + 6);
												ghost.setAttribute("y", cy - ghostH / 2);
												svg.appendChild(line);
												svg.appendChild(ghost);
												linking = { sourceId, line, ghost, ghostH };
												$(document)
													.on("mousemove.ganttlink", onMouseMove)
													.on("mouseup.ganttlink", onMouseUp)
													.on("keydown.ganttlink", (ev) => { if (ev.key === "Escape") clearLinking(); });
											});
										};
										setTimeout(setup_dependency_linking, 150);
										
										g_cont.on('wheel', function(e) {
											if (e.originalEvent.deltaY !== 0 && !e.originalEvent.shiftKey) {
												e.stopPropagation();
											}
										});

										gantt_wrapper.find('.btn-export-gantt').off('click').on('click', function() {
											frappe.require('https://cdnjs.cloudflare.com/ajax/libs/dom-to-image/2.6.0/dom-to-image.min.js', function() {
												var node = g_cont[0];
												if (!node) return;

												var width = node.scrollWidth;
												var height = node.scrollHeight;
												var scale = 2;

												var $btn = gantt_wrapper.find('.btn-export-gantt');
												var original_html = $btn.html();
												$btn.prop('disabled', true).html('<i class="fa fa-spinner fa-spin mr-1"></i> Exporting...');

												domtoimage.toPng(node, {
													bgcolor: '#fff',
													width: width * scale,
													height: height * scale,
													style: {
														transform: 'scale(' + scale + ')',
														'transform-origin': 'top left',
														width: width + 'px',
														height: height + 'px',
														overflow: 'visible'
													},
													filter: function(el) {
														if (el.classList && (el.classList.contains('popup-wrapper') || el.classList.contains('custom-gantt-popup'))) {
															return false;
														}
														return true;
													}
												}).then(function(url) {
													var link = document.createElement('a');
													link.download = 'Gantt-' + frm.doc.name + '-' + moment().format('YYYYMMDD') + '.png';
													link.href = url;
													link.click();
													$btn.prop('disabled', false).html(original_html);
												}).catch(function(err) {
													console.error("Gantt Export Error:", err);
													frappe.show_alert({ message: __("Gantt export failed"), indicator: 'red' });
													$btn.prop('disabled', false).html(original_html);
												});
											});
										});

										// Backup scroll: find the earliest task bar and scroll so it
										// appears near the left edge with a small margin. Used when the
										// first task's date can't be resolved via the library API.
										const scroll_to_first_task = () => {
											const real_container = g_cont[0];
											if (!real_container) return;
											const bars = real_container.querySelectorAll("rect.bar");
											if (!bars.length) return;
											let min_x = Infinity;
											bars.forEach((bar) => {
												const x = parseFloat(bar.getAttribute("x"));
												if (!isNaN(x) && x < min_x) min_x = x;
											});
											if (!isFinite(min_x)) return;
											real_container.scrollTo({ left: Math.max(0, min_x - 80), behavior: "smooth" });
										};
										// Re-run the auto-scroll to the first task's start date. The
										// library scrolls once during construction, but that can happen
										// before the Schedule tab is fully laid out (zero width), leaving
										// the viewport unmoved. Re-invoking the library's own
										// set_scroll_position with the date string lands the viewport on
										// the first task's date reliably; we call it a few times to win over
										// any late asynchronous layout/scroll. Falls back to the earliest
										// task bar when the date can't be resolved via the library API.
										const scroll_to_target = () => {
											if (first_task_start && gantt && typeof gantt.set_scroll_position === "function") {
												try {
													gantt.set_scroll_position(first_task_start);
													return;
												} catch (e) {
													// Library internals unavailable — fall back below.
												}
											}
											scroll_to_first_task();
										};
										if (preserveScroll && savedScrollLeft != null) {
											// Restore the pre-refresh scroll position. Done twice to win over
											// any late asynchronous scroll from the library on re-init.
											const restore = () => { const gc = g_cont[0]; if (gc) gc.scrollTo({ left: savedScrollLeft, top: savedScrollTop, behavior: "auto" }); };
											setTimeout(restore, 50);
											setTimeout(restore, 200);
										} else {
											// scroll_to in the options already aims at the first task's date;
											// these backups guarantee it lands once the tab is laid out.
											setTimeout(scroll_to_target, 350);
											setTimeout(scroll_to_target, 700);
										}
									} catch (e) { console.error("Gantt error:", e); chart_container.html('<p class="text-danger">Error initializing Gantt chart.</p>'); }
								} else { chart_container.html('<p class="text-muted text-center p-4">Error fetching data or no tasks found.</p>'); }
							},
						});
					}
				};
				wrapperField.__custom_gantt_bound = true;
			}
			wrapperField.render_heatmap = function(frm, container) {
				const body = container.find(".heatmap-body");
				body.html('<div class="p-2 text-center text-muted small">Loading allocation...</div>');
				frappe.call({
					method: "erpnext_enhancements.project_enhancements.page.project_dashboard.project_dashboard.get_resource_allocation_data",
					args: { project_name: frm.doc.name },
					callback: (r) => {
						if (r.message && Object.keys(r.message).length > 0) {
							container.show();
							const data = r.message; const users = Object.keys(data);
							let allDates = []; users.forEach(u => allDates.push(...Object.keys(data[u])));
							allDates = [...new Set(allDates)].sort();
							if (allDates.length === 0) { container.hide(); return; }
							let html = `<table class="heatmap-table"><thead><tr><th class="user-cell">Assignee</th>`;
							allDates.forEach(date => { html += `<th>${moment(date).format('DD MMM')}</th>`; });
							html += `</tr></thead><tbody>`;
							users.forEach(user => {
								html += `<tr><td class="user-cell">${user}</td>`;
								allDates.forEach(date => {
									const cell = data[user][date] || { hours: 0, tasks: [] };
									const hrs = cell.hours; let cls = hrs > 9 ? "workload-high" : (hrs >= 6 ? "workload-med" : (hrs > 0 ? "workload-low" : ""));
									html += `<td class="${cls}" data-user="${user}" data-date="${date}" title="${hrs.toFixed(1)} hrs">${hrs > 0 ? hrs.toFixed(1) : '-'}</td>`;
								});
								html += `</tr>`;
							});
							html += `</tbody></table>`; body.html(html);
							body.find('td:not(.user-cell)').on('click', function() {
								const u = $(this).data('user'); const d = $(this).data('date'); const c = data[u][d];
								if (c && c.tasks.length > 0) {
									$('.gantt .bar-wrapper').removeClass('highlight');
									c.tasks.forEach(t => { $(`.gantt .bar-wrapper[data-id="${t.id}"]`).addClass('highlight'); });
									frappe.show_alert({ message: `Highlighting ${c.tasks.length} tasks for ${u} on ${d}`, indicator: 'info' });
								}
							});
						} else { container.hide(); }
					}
				});
			};
			if (wrapperField.$wrapper) wrapperField.refresh();
		}
	},
});

frappe.ui.form.on("Project", {
	refresh: function (frm) {
		if (!frm.is_new()) {
			var field = frm.get_field("custom_reminder_action");
			if (field) {
				var $btn = $('<button class="btn btn-default btn-sm icon-btn"><span class="icon icon-sm"><svg class="es-icon es-line  icon-sm" aria-hidden="true"><use href="#es-line-bell"></use></svg></span> Set Reminder</button>');
				$btn.on("click", function () {
					var d = new frappe.ui.Dialog({
						title: __("Create a Reminder"),
						fields: [
							{ label: "Remind Me In", fieldname: "remind_in", fieldtype: "Select", options: [{ label: "30 Minutes", value: 30 }, { label: "1 Hour", value: 60 }, { label: "2 Hours", value: 120 }, { label: "4 Hours", value: 240 }, { label: "Tomorrow Morning", value: "tomorrow" }], onchange: function () { var choice = this.get_value(); if (!choice) return; var new_time; if (choice === "tomorrow") { new_time = moment().add(1, "days").set({ hour: 9, minute: 0, second: 0 }).format("YYYY-MM-DD HH:mm:ss"); } else { new_time = moment().add(choice, "minutes").format("YYYY-MM-DD HH:mm:ss"); } d.set_value("remind_at", new_time); } },
							{ fieldtype: "Column Break" },
							{ label: "Remind At", fieldname: "remind_at", fieldtype: "Datetime", reqd: 1, default: frappe.datetime.now_datetime() },
							{ fieldtype: "Section Break" },
							{ label: "Description", fieldname: "description", fieldtype: "Small Text", reqd: 1, default: "Reminder for Project: " + frm.doc.project_name },
						],
						primary_action_label: __("Create"),
						primary_action: function (values) {
							frappe.db.insert({ doctype: "ToDo", reference_type: frm.doc.doctype, reference_name: frm.doc.name, description: values.description, date: values.remind_at, allocated_to: frappe.session.user, status: "Open" }).then(() => { d.hide(); frappe.show_alert({ message: __("Reminder created successfully"), indicator: "green" }); });
						},
					});
					d.show();
				});
				field.$wrapper.empty().append($btn);
			}
		}
	},
});
