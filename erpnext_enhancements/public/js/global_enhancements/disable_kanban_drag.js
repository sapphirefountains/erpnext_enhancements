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

            // Process any cards that are already in the DOM.
            processCards(wrapper);

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
        }
    }
};
