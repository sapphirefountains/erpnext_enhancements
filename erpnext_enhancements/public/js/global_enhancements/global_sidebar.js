$(document).ready(function () {
	const HOME_ITEM_CLASS = "custom-home-sidebar-item";

	// Helper to update selection state based on current route
	const updateSelectionState = () => {
		const route = frappe.get_route();
		// Check if we are at root, /desk, or /desk/home
		const isHome = !route || route.length === 0 || (route.length === 1 && route[0] === "desk") || (route.length === 2 && route[0] === "desk" && route[1] === "home");

		const $homeItem = $(`.${HOME_ITEM_CLASS}`);
		if ($homeItem.length === 0) return;

		if (isHome) {
			// Select Home, Deselect others to visually indicate we are on Home
			$(".standard-sidebar-item").removeClass("selected");
			$homeItem.addClass("selected");
		} else {
			// Deselect Home (Frappe logic will handle selecting the active workspace/item)
			$homeItem.removeClass("selected");
		}
	};

	// Helper to inject the Home item
	const injectHomeItem = () => {
		// Find ALL "Workspaces" sidebar items (in case of multiple sidebars/zombies)
		// We filter .sidebar-item-label to find the specific "Workspaces" entry
		const $workspacesLabels = $('.sidebar-item-label').filter(function () {
			return $(this).text().trim().toLowerCase() === "workspaces";
		});

		// If Workspaces item isn't rendered yet, we can't clone it or place before it
		if ($workspacesLabels.length === 0) return;

		$workspacesLabels.each(function () {
			const $label = $(this);
			const $workspacesItem = $label.closest(".standard-sidebar-item");
			if ($workspacesItem.length === 0) return;

			// Check if we already injected BEFORE this specific item
			const $prev = $workspacesItem.prev();
			if ($prev.hasClass(HOME_ITEM_CLASS)) {
				return; // Already present for this instance
			}

			// 3. Clone the Workspaces item to inherit styles and structure
			const $homeItem = $workspacesItem.clone();
			$homeItem.addClass(HOME_ITEM_CLASS);
			$homeItem.removeClass("selected"); // Default to unselected

			// Update label
			$homeItem.find(".sidebar-item-label").text("Home");

			// Update icon
			const $iconUse = $homeItem.find(".icon use");
			if ($iconUse.length > 0) {
				$iconUse.attr("href", "#icon-home");
			} else {
				// Fallback: try to replace the icon container content if structure is different
				const $iconContainer = $homeItem.find(".sidebar-item-icon");
				if ($iconContainer.length > 0) {
					$iconContainer.html(frappe.utils.icon("home", "md"));
				}
			}

			// Remove existing listeners from the clone and prevent default link behavior
			$homeItem.off("click");
			$homeItem.find("a").attr("href", "#");

			// Insert the Home item BEFORE the Workspaces item
			$workspacesItem.before($homeItem);
		});

		// Set initial selection state
		updateSelectionState();
	};

	// Event Delegation for Click on the new Home button
	$(document).on("click", `.${HOME_ITEM_CLASS}`, function (e) {
		e.preventDefault();
		e.stopPropagation();
		frappe.set_route("desk", "home");
		// Immediate visual feedback
		updateSelectionState();
	});

	// Wait for Frappe to be ready before starting logic
	const runWhenFrappeReady = (callback) => {
		if (window.frappe && window.frappe.ui && window.frappe.router) {
			callback();
		} else {
			setTimeout(() => runWhenFrappeReady(callback), 100);
		}
	};

	runWhenFrappeReady(() => {
		// 1. Inject immediately if possible
		injectHomeItem();

		// 3. Listen for route changes to re-check injection and selection
		frappe.router.on('change', () => {
			// Small delay to allow sidebar re-render if it happens on navigation
			setTimeout(injectHomeItem, 200);
		});

		// 4. Polling fallback: Check periodically to ensure button persists
		// This handles cases where the sidebar is re-rendered without a route change
		// or if the initial injection missed.
		setInterval(injectHomeItem, 1000);
	});
});
