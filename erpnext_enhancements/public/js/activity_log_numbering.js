frappe.provide("erpnext_enhancements.activity");

erpnext_enhancements.activity.counts = {};
erpnext_enhancements.activity.current_key = null;

erpnext_enhancements.activity.fetch_counts = function(frm) {
    if (!frm || !frm.doc) return;
    const key = `${frm.doc.doctype}::${frm.doc.name}`;

    // Check if already fetched for this exact document recently?
    // We want to re-fetch if we just saved/added comment.
    // For now, simple caching to avoid infinite loops in observer.
    // We can invalidate cache on "refresh" event.
    if (erpnext_enhancements.activity.current_key === key && erpnext_enhancements.activity.counts[key]) {
        return;
    }

    erpnext_enhancements.activity.current_key = key;

    frappe.call({
        method: "erpnext_enhancements.api.activity_log.get_activity_counts",
        args: {
            doctype: frm.doc.doctype,
            docname: frm.doc.name
        },
        callback: function(r) {
            if (r.message) {
                erpnext_enhancements.activity.counts[key] = r.message;
                // Trigger re-render
                erpnext_enhancements.activity.apply_numbering();
            }
        }
    });
};

erpnext_enhancements.activity.apply_numbering = function() {
    const containers = document.querySelectorAll('.timeline-items');
    if (!containers.length) return;

    // Ensure we have counts for current form
    const frm = window.cur_frm;
    if (frm && frm.doc) {
         erpnext_enhancements.activity.fetch_counts(frm);
    }

    const key = frm ? `${frm.doc.doctype}::${frm.doc.name}` : null;
    const counts = (key && erpnext_enhancements.activity.counts[key]) ? erpnext_enhancements.activity.counts[key] : null;

    containers.forEach(container => {
        container.classList.add('activity-numbered');
        const items = Array.from(container.querySelectorAll('.timeline-item'));

        // Calculate Total Base
        let total = items.length; // Fallback

        if (counts) {
            // Determine filter state
            // Check for "Show all activity" checkbox
            // In standard Frappe, it's often a class .show-all-activity or similar in .timeline-head
            // We check if "Versions" are generally shown.
            // Best proxy: Check if any Version log is visible?
            // Or try to find the checkbox.
            // .action-btn.show-all-activity (v13?)

            // Let's look for standard toggle.
            const showAllToggle = document.querySelector('.show-all-activity input') ||
                                  document.querySelector('[data-action="show_all_activity"]');

            let isShowAll = false;
            if (showAllToggle) {
                 isShowAll = showAllToggle.checked || showAllToggle.classList.contains('active');
            } else {
                 // Fallback: If we see any visible version, assume Show All is ON
                 const visibleVersion = items.find(item => {
                     return (item.getAttribute('data-doctype') === 'Version' && item.offsetParent !== null);
                 });
                 if (visibleVersion) isShowAll = true;
            }

            let grandTotal = (counts.Comment || 0) + (counts.Communication || 0);
            if (isShowAll) {
                grandTotal += (counts.Version || 0);
            }

            // Use Math.max to ensure we don't have negative relative numbers if DOM has more items than backend (latency)
            // But we must count VISIBLE items in DOM to compare.
            // If DOM has 5 visible. GrandTotal says 3. Numbering 3,2,1.
            // If DOM has 5 visible. GrandTotal says 10. Numbering 10..6.
            total = Math.max(grandTotal, items.filter(i => i.offsetParent !== null).length);
        }

        let visibleIndex = 0;
        items.forEach((item) => {
             // Check visibility
             // offsetParent is null if display:none
             if (item.offsetParent === null) return;

             // Standard relative numbering: Top = Total, Bottom = 1.
             // visibleIndex 0 (Top) -> Number = Total.
             const number = total - visibleIndex;
             visibleIndex++;

             // Update DOM
             let numEl = item.querySelector('.activity-number');
             if (!numEl) {
                numEl = document.createElement('div');
                numEl.className = 'activity-number';
                item.prepend(numEl);
             }
             if (numEl.textContent != '#' + number) {
                numEl.textContent = '#' + number;
             }
        });
    });
};

// Debounce function
function debounce(func, wait) {
    let timeout;
    return function() {
        const context = this, args = arguments;
        clearTimeout(timeout);
        timeout = setTimeout(() => func.apply(context, args), wait);
    };
}

const debounced_apply = debounce(() => {
    erpnext_enhancements.activity.apply_numbering();
}, 200);

// Global Observer
const observer = new MutationObserver((mutations) => {
    let shouldUpdate = false;
    for (const mutation of mutations) {
        if (mutation.type === 'childList') {
            // Check if timeline or activity items are involved
            if (mutation.target.closest && mutation.target.closest('.timeline-items')) {
                shouldUpdate = true;
                break;
            }
            // Check added nodes
            if (mutation.addedNodes.length) {
                for (const node of mutation.addedNodes) {
                    if (node.nodeType === 1) {
                         if (node.classList && (node.classList.contains('timeline-items') || node.classList.contains('timeline-item'))) {
                             shouldUpdate = true;
                             break;
                         }
                    }
                }
            }
        }
        // Also observe class changes on checkbox?
        // Or if filter changes, DOM changes (items hidden/shown).
    }

    if (shouldUpdate) {
        debounced_apply();
    }
});

// Hook into Form Refresh to invalidate cache
$(document).on('form-refresh', function(e, frm) {
    if (frm && frm.doc) {
        const key = `${frm.doc.doctype}::${frm.doc.name}`;
        // Force refresh of counts
        delete erpnext_enhancements.activity.counts[key];
        erpnext_enhancements.activity.current_key = null;
        erpnext_enhancements.activity.fetch_counts(frm);
    }
});

// Initialize
$(document).ready(function() {
    erpnext_enhancements.activity.apply_numbering();
    observer.observe(document.body, { childList: true, subtree: true });
});
