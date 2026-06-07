/* global erpnext_enhancements */
frappe.provide("erpnext_enhancements.dashboard_api");

/**
 * A wrapper around frappe.call to support abort controllers and timeouts.
 *
 * @param {Object} options Options passed to frappe.call
 * @param {AbortSignal} signal AbortController signal
 * @returns {Promise<any>}
 */
erpnext_enhancements.dashboard_api.call = function (options, signal = null) {
	return new Promise((resolve, reject) => {
		let isSettled = false;
		let timeoutId = null;

		// Set up 8000ms timeout
		const TIMEOUT_MS = 8000;

		// Helper to settle the promise and clean up
		const settle = () => {
			if (isSettled) return true;
			isSettled = true;
			if (timeoutId) clearTimeout(timeoutId);
			if (signal) signal.removeEventListener("abort", abortHandler);
			return false;
		};

		// frappe.call returns a jQuery Promise (jqXHR)
		const jqXHR = frappe.call({
			...options,
			callback: function (r) {
				if (settle()) return;

				if (options.callback) {
					options.callback(r);
				}

				resolve(r);
			},
			error: function (r) {
				if (settle()) return;

				if (options.error) {
					options.error(r);
				}

				reject(r);
			},
		});

		// Handle the Abort Signal
		const abortHandler = () => {
			if (settle()) return;

			if (jqXHR && typeof jqXHR.abort === "function") {
				jqXHR.abort();
			}

			const error = new Error("Request aborted");
			error.name = "CancellationError";
			reject(error);
		};

		if (signal) {
			if (signal.aborted) {
				abortHandler();
				return;
			}
			signal.addEventListener("abort", abortHandler);
		}

		// Handle the Timeout
		timeoutId = setTimeout(() => {
			if (settle()) return;

			if (jqXHR && typeof jqXHR.abort === "function") {
				jqXHR.abort();
			}

			const error = new Error(`Request timed out after ${TIMEOUT_MS}ms`);
			error.name = "TimeoutError";
			reject(error);
		}, TIMEOUT_MS);
	});
};
