frappe.provide("frappe.listview_settings");

frappe.listview_settings['Opportunity'] = {
	// Force-fetch these fields even if not in the list view/kanban settings
	add_fields: ["expected_closing", "status"],
	refresh: function (listview) {
		if (listview.view_name === 'Kanban') {
			inject_kanban_custom_css();
			setup_kanban_color_observer(listview);
		}
	}
};

function inject_kanban_custom_css() {
	if (document.getElementById('kanban-opportunity-colors')) return;
	const css = `
		.kanban-card-wrapper[data-date-status="overdue"] .kanban-card, 
		.kanban-card-wrapper[data-date-status="overdue"] .kanban-card-body { 
			background-color: #ffebee !important; 
		}
		.kanban-card-wrapper[data-date-status="soon"] .kanban-card, 
		.kanban-card-wrapper[data-date-status="soon"] .kanban-card-body { 
			background-color: #fff9c4 !important; 
		}
		.kanban-card-wrapper[data-doc-status="excluded"] .kanban-card, 
		.kanban-card-wrapper[data-doc-status="excluded"] .kanban-card-body {
			background-color: var(--card-bg) !important;
		}
	`;
	const style = document.createElement('style');
	style.id = 'kanban-opportunity-colors';
	style.innerHTML = css;
	document.head.appendChild(style);
}

function setup_kanban_color_observer(listview) {
	const targetNode = listview.$result ? listview.$result.get(0) : document.body;

	// Initial run
	setTimeout(() => {
		apply_kanban_colors(listview);
	}, 1000);

	const observer = new MutationObserver(() => {
		apply_kanban_colors(listview);
	});

	observer.observe(targetNode, { childList: true, subtree: true });

	$(document).on('route-change', () => {
		observer.disconnect();
	});
}

function apply_kanban_colors(listview) {
	const data = listview.data || (listview.kanban && listview.kanban.data);
	
	if (!data || data.length === 0) return;

	const today = frappe.datetime.get_today();
	const next_7_days = frappe.datetime.add_days(today, 7);

	const dataMap = {};
	data.forEach(doc => {
		dataMap[doc.name] = doc;
	});

	$('.kanban-card-wrapper').each(function() {
		const $wrapper = $(this);
		const card_name = $wrapper.attr('data-name');

		if (!card_name || !dataMap[card_name]) return;

		const doc = dataMap[card_name];

		// 1. Status Check
		const is_excluded = ["Closed Won", "Lost", "Closed Lost"].includes(doc.status);
		$wrapper.attr('data-doc-status', is_excluded ? 'excluded' : 'active');

		if (is_excluded) {
			$wrapper.removeAttr('data-date-status');
			return;
		}

		// 2. Date Logic
		if (doc.expected_closing) {
			if (doc.expected_closing < today) {
				$wrapper.attr('data-date-status', 'overdue');
			} else if (doc.expected_closing >= today && doc.expected_closing <= next_7_days) {
				$wrapper.attr('data-date-status', 'soon');
			} else {
				$wrapper.removeAttr('data-date-status');
			}
		} else {
			$wrapper.removeAttr('data-date-status');
		}
	});
}
