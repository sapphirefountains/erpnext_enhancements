frappe.provide("erpnext_enhancements.filter_help");

(function() {
    // Utility to wait for element
    const waitFor = (selector, callback, maxAttempts = 20, interval = 500) => {
        // Check immediately
        const immediateEl = $(selector);
        if (immediateEl.length > 0) {
            callback(immediateEl);
            return;
        }

        let attempts = 0;
        const i = setInterval(() => {
            attempts++;
            const el = $(selector);
            if (el.length > 0) {
                clearInterval(i);
                callback(el);
            } else if (attempts >= maxAttempts) {
                clearInterval(i);
            }
        }, interval);
    };

    // Main Function to Inject Button
    const inject_filter_help = () => {
        // We look for the filter button.
        // Strategy 1: .filter-selector button (Standard List View)
        // Strategy 2: button containing "Filter"

        const selector = '.filter-selector, .standard-filter-section';

        waitFor(selector, ($container) => {
            // Check if already injected
            if ($container.find('.filter-help-btn').length > 0) return;

            // Find the filter button within the container
            const $filterBtn = $container.find('button.filter-button, .btn-filter, button:contains("Filter")').first();

            if ($filterBtn.length > 0) {
                const $helpBtn = $(`
                    <button class="btn btn-default btn-xs filter-help-btn" title="${__('How to use filters')}">
                        <span class="filter-help-icon">?</span>
                    </button>
                `);

                // Inject after the filter button
                $filterBtn.after($helpBtn);

                // Click Handler
                $helpBtn.on('click', (e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    show_filter_help_dialog();
                });
            }
        });
    };

    // Dialog Content
    const show_filter_help_dialog = () => {
        const dialog = new frappe.ui.Dialog({
            title: __("How to use Filters"),
            size: 'large',
            fields: [
                {
                    fieldtype: 'HTML',
                    fieldname: 'help_content',
                    options: `
                        <div class="filter-help-content" style="padding: 10px;">
                            <p>${__("The filter system allows you to narrow down your list to find specific records.")}</p>

                            <div class="filter-example-visual" style="background: #f8f9fa; padding: 20px; border-radius: 8px; border: 1px solid #d1d8dd; margin-bottom: 20px;">
                                <div style="display: flex; align-items: center; gap: 10px; font-family: monospace; font-size: 13px;">

                                    <!-- Field Column -->
                                    <div style="flex: 1; text-align: center;">
                                        <div style="background: white; border: 1px solid #d1d8dd; padding: 8px; border-radius: 4px; box-shadow: 0 1px 2px rgba(0,0,0,0.05);">
                                            <strong>ID</strong>
                                        </div>
                                        <div style="margin-top: 8px; color: #666; font-size: 11px;">
                                            <span style="display: block;">Column 1</span>
                                            <strong>What to filter</strong>
                                        </div>
                                    </div>

                                    <!-- Condition Column -->
                                    <div style="flex: 1; text-align: center;">
                                        <div style="background: white; border: 1px solid #d1d8dd; padding: 8px; border-radius: 4px; box-shadow: 0 1px 2px rgba(0,0,0,0.05);">
                                            <strong>Equals</strong>
                                        </div>
                                        <div style="margin-top: 8px; color: #666; font-size: 11px;">
                                            <span style="display: block;">Column 2</span>
                                            <strong>How to filter</strong>
                                            <a href="#" class="filter-condition-help-link" style="margin-left: 5px; text-decoration: none;" title="${__('Click to see filter options')}">❓</a>
                                        </div>
                                    </div>

                                    <!-- Value Column -->
                                    <div style="flex: 1; text-align: center;">
                                        <div style="background: white; border: 1px solid #d1d8dd; padding: 8px; border-radius: 4px; box-shadow: 0 1px 2px rgba(0,0,0,0.05);">
                                            <strong>12345</strong>
                                        </div>
                                        <div style="margin-top: 8px; color: #666; font-size: 11px;">
                                            <span style="display: block;">Column 3</span>
                                            <strong>Value</strong>
                                        </div>
                                    </div>

                                </div>
                            </div>

                            <div class="filter-explanation">
                                <p><strong>1. First Column (Field):</strong> Select the field you want to filter by (e.g., Status, ID, Date).</p>
                                <p><strong>2. Second Column (Condition):</strong> Select the rule or condition (e.g., Equals, Not Equals, Like).</p>
                                <p><strong>3. Third Column (Value):</strong> Enter the value you are looking for.</p>
                                <p class="text-muted"><small><em>Tip: You can add multiple filters to further narrow down your results.</em></small></p>
                            </div>

                            <div class="filter-condition-details" style="display: none; margin-top: 15px; border-top: 1px solid #d1d8dd; padding-top: 15px;">
                                <h5>${__("Filter Conditions Explained")}</h5>
                                <div style="display: flex; gap: 20px; flex-wrap: wrap;">
                                    <div style="flex: 1; min-width: 250px;">
                                        <h6>${__("Standard Filters")}</h6>
                                        <ul style="padding-left: 20px; font-size: 12px; color: #36414c; line-height: 1.6;">
                                            <li><strong>Equals:</strong> ${__("The field value must exactly match the value you enter.")}</li>
                                            <li><strong>Not Equals:</strong> ${__("The field value must not be the same as the value you enter.")}</li>
                                            <li><strong>Like:</strong> ${__("Partial match. Finds records where the field contains the text you enter.")}</li>
                                            <li><strong>Not Like:</strong> ${__("Finds records where the field does not contain the text you enter.")}</li>
                                            <li><strong>In:</strong> ${__("Matches if the value is one of multiple values (separate values with commas).")}</li>
                                            <li><strong>Not In:</strong> ${__("Matches if the value is not in the list of values you provide.")}</li>
                                            <li><strong>Is:</strong> ${__("Used to check if a field is 'Set' (has a value) or 'Not Set' (is empty).")}</li>
                                        </ul>
                                    </div>
                                    <div style="flex: 1; min-width: 250px;">
                                        <h6>${__("Time-based Filters (for Dates)")}</h6>
                                        <ul style="padding-left: 20px; font-size: 12px; color: #36414c; line-height: 1.6;">
                                            <li><strong>Is:</strong> ${__("Matches a specific date.")}</li>
                                            <li><strong>After / Before:</strong> ${__("Matches dates after or before a specific date.")}</li>
                                            <li><strong>On or After / On or Before:</strong> ${__("Inclusive comparison.")}</li>
                                            <li><strong>Between:</strong> ${__("Matches dates within a range (Start to End).")}</li>
                                            <li><strong>Timespan:</strong> ${__("Select from presets like 'Today', 'This Week', 'Last Month'.")}</li>
                                            <li><strong>Fiscal Year:</strong> ${__("Matches dates within a specific financial year.")}</li>
                                        </ul>
                                        <p class="text-muted" style="font-size: 11px; margin-top: 10px;"><em>${__("Note: The available conditions change depending on whether you select a text field or a date field in the first column.")}</em></p>
                                    </div>
                                </div>
                            </div>
                        </div>
                    `
                }
            ]
        });

        dialog.show();

        // Bind click event to toggle details
        dialog.$wrapper.find('.filter-condition-help-link').on('click', function(e) {
            e.preventDefault();
            dialog.$wrapper.find('.filter-condition-details').slideToggle();
        });
    };

    // Hook into Page Renders
    $(document).on('app_ready', () => {
        inject_filter_help();
    });

    // Also watch for route changes (ListView rendering)
    if (frappe.router) {
        frappe.router.on('change', () => {
            inject_filter_help();
        });
    }

})();
