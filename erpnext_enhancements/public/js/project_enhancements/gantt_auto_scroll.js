/**
 * @file This script provides a robust auto-scrolling functionality for Frappe Gantt charts.
 * @description It uses a MutationObserver to detect when a Gantt chart is added to the DOM.
 * Once a chart is detected, a nested MutationObserver waits for the bar elements to be
 * rendered, then smoothly scrolls to the earliest task so the user can see where the
 * project starts.
 */
document.addEventListener("DOMContentLoaded", () => {
	const GANTT_SCROLL_LOGIC = {
		/**
		 * Initiates the scroll to the earliest task bar within the Gantt chart.
		 * @param {HTMLElement} gantt_container - The container of the Gantt chart SVG.
		 */
		scrollToFirstTask: function (gantt_container) {
			const bar_els = gantt_container.querySelectorAll("rect.bar");
			if (!bar_els.length) return;

			const scroll_el =
				gantt_container.closest(".gantt-scroll-wrapper") ||
				gantt_container.closest(".gantt-container");
			if (!scroll_el) return;

			// Find the bar with the smallest x value (earliest task).
			let min_x = Infinity;
			bar_els.forEach((bar) => {
				const x = parseFloat(bar.getAttribute("x"));
				if (!isNaN(x) && x < min_x) min_x = x;
			});

			if (!isFinite(min_x)) return;

			this.executeScroll(scroll_el, min_x);
		},

		/**
		 * Calculates the target scroll position and performs the scroll.
		 * @param {HTMLElement} scroll_el - The scrollable container element.
		 * @param {number} target_x - The x position of the earliest task bar.
		 */
		executeScroll: function (scroll_el, target_x) {
			// Scroll so the earliest task has a small left margin (80px).
			const scroll_left = Math.max(0, target_x - 80);

			scroll_el.scrollTo({
				left: scroll_left,
				behavior: "smooth",
			});
		},
	};

	/**
	 * Watches for bar elements to be added to the Gantt container.
	 * @param {HTMLElement} ganttContainer - The Gantt chart's main container element.
	 */
	function waitForTodayHighlight(ganttContainer) {
		// First, check if bars already exist.
		if (ganttContainer.querySelector("rect.bar")) {
			GANTT_SCROLL_LOGIC.scrollToFirstTask(ganttContainer);
			return;
		}

		// If not, set up an observer to wait for them.
		const observer = new MutationObserver((mutations, obs) => {
			for (const mutation of mutations) {
				if (mutation.addedNodes.length > 0) {
					if (ganttContainer.querySelector("rect.bar")) {
						GANTT_SCROLL_LOGIC.scrollToFirstTask(ganttContainer);
						obs.disconnect(); // Clean up the observer once the job is done.
						return;
					}
				}
			}
		});

		observer.observe(ganttContainer, {
			childList: true,
			subtree: true,
		});
	}

	/**
	 * The main observer that watches for Gantt charts being added to the document body.
	 */
	const bodyObserver = new MutationObserver((mutations) => {
		// Optimization: Only run on pages that might have a Gantt chart.
		// This prevents unnecessary processing on heavy pages like "Purchase Order".
		if (window.frappe && window.frappe.get_route) {
			const route = frappe.get_route();
			if (Array.isArray(route)) {
				const isRelevantPage = route.some((part) => {
					if (typeof part !== "string") return false;
					const p = part.toLowerCase();
					return p === "project" || p === "task" || p === "project_dashboard";
				});

				if (!isRelevantPage) return;
			}
		}

		mutations.forEach((mutation) => {
			mutation.addedNodes.forEach((node) => {
				if (node.nodeType === 1) {
					// Ensure it's an element node.
					// Check if the added node is a Gantt container or contains one.
					const ganttContainer = node.matches(".gantt-container")
						? node
						: node.querySelector(".gantt-container");

					if (ganttContainer) {
						// Once the container is found, start the nested observation.
						waitForTodayHighlight(ganttContainer);
					}
				}
			});
		});
	});

	bodyObserver.observe(document.body, {
		childList: true,
		subtree: true,
	});
});
