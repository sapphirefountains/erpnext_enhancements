(function() {
  const selectors = [
    'link[rel="preload"][href*="timeless/icons.svg"]',
    'link[rel="preload"][href*="espresso/icons.svg"]',
  ];

  const observer = new MutationObserver((mutations) => {
    mutations.forEach((mutation) => {
      mutation.addedNodes.forEach((node) => {
        if (node.nodeType === Node.ELEMENT_NODE) {
          selectors.forEach((selector) => {
            if (node.matches(selector)) {
              node.remove();
            }
          });
        }
      });
    });
  });

  observer.observe(document.head, {
    childList: true,
  });
})();
