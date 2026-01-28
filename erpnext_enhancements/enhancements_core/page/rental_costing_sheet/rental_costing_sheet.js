frappe.pages['rental-costing-sheet'].on_page_load = function(wrapper) {
	var page = frappe.ui.make_app_page({
		parent: wrapper,
		title: 'Rental Costing Sheet',
		single_column: true
	});

	// Load the template
	$(wrapper).find('.layout-main-section').append(frappe.render_template('rental_costing_sheet', {}));

	// Load Vue if not already globally available, then init
	// We check for Vue 3 (Vue.createApp) or Vue 2 (new Vue)
	if (typeof Vue === 'undefined') {
		frappe.require('/assets/erpnext_enhancements/js/vue.global.js', function() {
			init_rental_costing_vue(wrapper);
		});
	} else {
		init_rental_costing_vue(wrapper);
	}
};

function init_rental_costing_vue(wrapper) {
	const { createApp, reactive, computed, watch, onMounted } = Vue;

	const app = createApp({
		setup() {
			// --- State ---
			const state = reactive({
				sections: [
					{
						title: "Rental Fountain",
						items: []
					},
					{
						title: "Technician Travel Cost",
						items: []
					},
					{
						title: "Shipping",
						items: []
					},
					{
						title: "System-5",
						items: []
					}
				],
				project_name: "",
				customer: ""
			});

			// --- Computed Totals ---
			const grand_total_cost = computed(() => {
				let total = 0;
				state.sections.forEach(section => {
					section.items.forEach(item => {
						total += (item.qty || 0) * (item.quote || 0);
					});
				});
				return total;
			});

			const grand_total_price = computed(() => {
				let total = 0;
				state.sections.forEach(section => {
					section.items.forEach(item => {
						total += calculate_sub_total(item);
					});
				});
				return total;
			});

			const total_markup_value = computed(() => {
				return grand_total_price.value - grand_total_cost.value;
			});

			// --- Helpers ---
			const calculate_markup_per_unit = (item) => {
				// Logic: Quote * Markup %
				return (item.quote || 0) * ((item.markup_percent || 0) / 100);
			};

			const calculate_unit_total = (item) => {
				// Logic: Quote + Markup/Unit
				return (item.quote || 0) + calculate_markup_per_unit(item);
			};

			const calculate_sub_total = (item) => {
				// Logic: Unit Total * Qty
				return calculate_unit_total(item) * (item.qty || 0);
			};

			const add_row = (section_index) => {
				state.sections[section_index].items.push({
					job_expense: "",
					vendor: "",
					description: "",
					qty: 1,
					unit: "Nos",
					quote: 0,
					markup_percent: 20, // Default 20%
					notes: ""
				});
			};

			const remove_row = (section_index, item_index) => {
				state.sections[section_index].items.splice(item_index, 1);
			};

			const format_currency = (value) => {
				return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(value);
			};

			// --- Persistence ---
			const save_local = () => {
				localStorage.setItem('rental_costing_sheet_data', JSON.stringify(state));
				frappe.show_alert({message: 'Saved locally', indicator: 'green'});
			};

			const load_local = () => {
				const data = localStorage.getItem('rental_costing_sheet_data');
				if (data) {
					const parsed = JSON.parse(data);
					state.sections = parsed.sections || state.sections;
					state.project_name = parsed.project_name || "";
					state.customer = parsed.customer || "";
				} else {
					// Add default empty rows if new
					state.sections.forEach((s, i) => add_row(i));
				}
			};

			const clear_sheet = () => {
				frappe.confirm('Are you sure you want to clear the sheet?', () => {
					state.sections.forEach(s => s.items = []);
					state.sections.forEach((s, i) => add_row(i));
					state.project_name = "";
					state.customer = "";
					save_local();
				});
			}

			const export_csv = () => {
				let csvContent = "data:text/csv;charset=utf-8,";
				csvContent += "Section,Job Expense,Vendor,Description,Qty,Unit,Quote (Cost),Total Cost,Markup %,Markup/Unit,Unit Price,Sub-Total Price,Notes\n";

				state.sections.forEach(section => {
					section.items.forEach(item => {
						let row = [
							`"${section.title}"`,
							`"${item.job_expense || ''}"`,
							`"${item.vendor || ''}"`,
							`"${item.description || ''}"`,
							item.qty,
							item.unit,
							item.quote,
							(item.qty * item.quote).toFixed(2),
							item.markup_percent,
							calculate_markup_per_unit(item).toFixed(2),
							calculate_unit_total(item).toFixed(2),
							calculate_sub_total(item).toFixed(2),
							`"${item.notes || ''}"`
						];
						csvContent += row.join(",") + "\n";
					});
				});

				const encodedUri = encodeURI(csvContent);
				const link = document.createElement("a");
				link.setAttribute("href", encodedUri);
				link.setAttribute("download", `Rental_Costing_${state.project_name || 'Sheet'}.csv`);
				document.body.appendChild(link);
				link.click();
				document.body.removeChild(link);
			};

			// --- Lifecycle ---
			onMounted(() => {
				load_local();
			});

			// Auto-save on change (debounced slightly by nature of Vue reactivity, but explicit watch is better)
			watch(() => state, () => {
				localStorage.setItem('rental_costing_sheet_data', JSON.stringify(state));
			}, { deep: true });

			return {
				state,
				grand_total_cost,
				grand_total_price,
				total_markup_value,
				add_row,
				remove_row,
				calculate_markup_per_unit,
				calculate_unit_total,
				calculate_sub_total,
				format_currency,
				clear_sheet,
				export_csv,
				save_local
			};
		}
	});

	app.mount($(wrapper).find('#rental-costing-app')[0]);
}
