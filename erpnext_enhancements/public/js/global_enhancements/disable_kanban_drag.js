// This script disables drag-and-drop for Kanban cards in the "Opportunity" doctype.

frappe.views.KanbanView = class extends frappe.views.KanbanView {
    render() {
        super.render();

        if (this.doctype === 'Opportunity') {
            const wrapper = this.wrapper[0] || this.wrapper; // Handle jQuery and standard elements

            const disableCardDragging = (card) => {
                card.style.cursor = 'default';
                card.setAttribute('draggable', 'false');
            };

            const processCards = (container) => {
                container.querySelectorAll('.kanban-card').forEach(disableCardDragging);
            };

            // Process any cards already in the DOM (idempotent, fine to run each render).
            processCards(wrapper);

            // Bind the dragstart blocker and MutationObserver ONCE per view instance.
            // render() runs on every refresh; previously each refresh added a duplicate
            // listener and a never-disconnected observer (memory + CPU leak). A single
            // subtree observer already catches every card added by later refreshes.
            if (this._opp_drag_bound) return;
            this._opp_drag_bound = true;

            // Prevent the dragstart event to disable dragging.
            wrapper.addEventListener('dragstart', (e) => {
                if (e.target.classList.contains('kanban-card')) {
                    e.preventDefault();
                    e.stopPropagation();
                    return false;
                }
            });

            // Use a MutationObserver to handle dynamically added cards.
            const observer = new MutationObserver((mutationsList) => {
                for (const mutation of mutationsList) {
                    if (mutation.type === 'childList') {
                        mutation.addedNodes.forEach(node => {
                            if (node.nodeType === 1) { // Check if it's an element
                                if (node.classList.contains('kanban-card')) {
                                    disableCardDragging(node);
                                }
                                // Also check for cards inside the added node.
                                processCards(node);
                            }
                        });
                    }
                }
            });

            // Start observing the wrapper for future changes.
            observer.observe(wrapper, { childList: true, subtree: true });
            this._opp_drag_observer = observer;
        }
    }
};
