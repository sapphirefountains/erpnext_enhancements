/**
 * performance_fixes.js — strip redundant icon-sprite preloads.
 *
 * Targets: the desk page <head>, globally.
 * Loaded via: hooks.py `app_include_js` (global desk script).
 *
 * A MutationObserver on document.head removes the `<link rel="preload">` tags
 * Frappe injects for the timeless/espresso icon sprites. These preloads warn in
 * the console (preloaded-but-unused) and waste a request; removing them as they
 * are added is a lightweight cleanup.
 */
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
