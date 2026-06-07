/**
 * @file This script provides a universal solution for auto-scrolling Frappe Gantt charts.
 * @description It uses a MutationObserver to detect any Gantt chart rendered on any
 * page within the Frappe Desk. Once a chart is detected, it programmatically scrolls
 * the timeline to center on the current date ('today'). This approach is robust and
 * works for the standard Task Gantt as well as custom Gantt implementations.
 */

(function () {
	"use strict";

	const observer = new MutationObserver((mutationsList, observer) => {
		for (const mutation of mutationsList) {
			if (mutation.type === "childList" && mutation.addedNodes.length > 0) {
				const gantt_container = document.querySelector(".gantt-container");

				if (gantt_container && !gantt_container.dataset.scrolled) {
					gantt_container.dataset.scrolled = "true";

					const today_el = gantt_container.querySelector(".today-highlight");

					if (today_el) {
						// Determine the correct scrolling element. The Project Dashboard
						// uses a wrapper, while the standard Task Gantt does not.
						const scroll_container =
							gantt_container.closest(".gantt-scroll-wrapper") || gantt_container;

						const container_width = scroll_container.clientWidth;
						const element_rect = today_el.getBoundingClientRect();
						const container_rect = scroll_container.getBoundingClientRect();

						const element_left_relative = element_rect.left - container_rect.left;
						const element_width = element_rect.width;

						const scroll_to_position =
							scroll_container.scrollLeft +
							element_left_relative -
							container_width / 2 +
							element_width / 2;

						scroll_container.scrollTo({
							left: scroll_to_position,
							behavior: "smooth",
						});
					}
				}
			}
		}
	});

	observer.observe(document.body, {
		childList: true,
		subtree: true,
	});
})();
