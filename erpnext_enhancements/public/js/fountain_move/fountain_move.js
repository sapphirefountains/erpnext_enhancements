/*
 * Public fountain-move intake form (/fountain-move).
 *
 * Vanilla JS, no framework — this page loads for anonymous members of the
 * public, often on a phone with poor signal standing next to a fountain, so
 * every kilobyte and every failure mode matters.
 *
 * Design rules that are load-bearing:
 *
 *  - Everything degrades. No Turnstile key, no Maps key, a dead Google script:
 *    the form still submits. The only hard dependency is fetch().
 *  - Photos are downscaled in the browser before upload. A modern phone photo
 *    is 4-12 MB; the server caps at 10 MB and mobile uploads are slow and
 *    flaky. Downscaling turns a 30-second upload into a 2-second one, and
 *    converts iOS HEIC to JPEG as a side effect (the server rejects HEIC,
 *    because Pillow cannot decode it without pillow-heif).
 *  - Server errors are shown verbatim when the server authored them, and
 *    replaced with plain language when it did not. See describeError().
 */

(function () {
	"use strict";

	var BOOT = window.FM_BOOT || {};
	var API = "/api/method/erpnext_enhancements.crm_enhancements.fountain_move.intake.";

	// Longest edge, in pixels, we upload. Comfortably enough detail to judge a
	// fountain and an access route; roughly a 10x size reduction from a phone.
	var MAX_EDGE = 1600;
	var JPEG_QUALITY = 0.82;

	var state = {
		sid: null,
		verdict: null,
		uploading: 0,
		submitting: false,
	};

	var form = document.getElementById("fm-form");
	var loading = document.getElementById("fm-loading");
	var errorBox = document.getElementById("fm-error");
	var submitBtn = document.getElementById("fm-submit");

	// ---------------------------------------------------------------- boot

	function init() {
		if (!form) return;
		loading.hidden = true;
		form.hidden = false;

		if (BOOT.prefill_location) {
			var select = form.querySelector('[name="purchase_location"]');
			if (select) select.value = BOOT.prefill_location;
		}

		form.addEventListener("submit", onSubmit);
		wirePhotoInputs();
		startTurnstile();
		startPlaces();
	}

	// ------------------------------------------------------------ turnstile

	function startTurnstile() {
		if (!BOOT.turnstile_sitekey) {
			// No key configured. The server will not auto-convert an unverified
			// submission, but it still keeps it — a customer must never lose
			// their work over our configuration.
			beginSession(null);
			return;
		}
		loadScript("https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit")
			.then(function () {
				if (!window.turnstile) throw new Error("turnstile unavailable");
				window.turnstile.render("#fm-turnstile", {
					sitekey: BOOT.turnstile_sitekey,
					action: "fountain-move-intake",
					callback: beginSession,
					"error-callback": function () {
						beginSession(null);
					},
					// Tokens are single-use and expire in ~5 minutes. Someone
					// filling in an address and taking two photos can easily
					// exceed that, so re-solve in place and refresh the session
					// rather than letting the submit fail at the last step.
					"expired-callback": function () {
						window.turnstile.reset();
					},
				});
			})
			.catch(function () {
				beginSession(null);
			});
	}

	function beginSession(token) {
		post("begin_intake", { turnstile_token: token, sid: state.sid, ref: BOOT.invite_token })
			.then(function (result) {
				state.sid = result.sid;
				state.verdict = result.verdict;
			})
			.catch(function () {
				/* Session comes back on submit if this failed transiently. */
			});
	}

	// --------------------------------------------------------------- places

	function startPlaces() {
		if (!BOOT.maps_api_key) return; // manual fields only — already visible

		var wrap = document.getElementById("fm-autocomplete");
		if (!wrap) return;

		bootstrapMaps(BOOT.maps_api_key)
			.then(function () {
				return google.maps.importLibrary("places");
			})
			.then(function (places) {
				// PlaceAutocompleteElement, not the legacy Autocomplete widget:
				// the old one is closed to new customers, so a freshly-minted
				// key may simply never initialise it.
				var el = new places.PlaceAutocompleteElement({
					includedRegionCodes: ["us"],
				});
				el.id = "fm-autocomplete-el";
				wrap.appendChild(el);
				document.getElementById("fm-autocomplete-wrap").hidden = false;

				el.addEventListener("gmp-select", function (event) {
					onPlaceSelected(event).catch(function (err) {
						warn("could not read the selected address", err);
					});
				});
			})
			.catch(function (err) {
				// The manual address fields are already on screen, so the form
				// still works — but this must NOT be silent. An earlier revision
				// swallowed it, and a TypeError here (see bootstrapMaps) looked
				// exactly like "no key configured" for days.
				warn("address autocomplete unavailable", err);
			});
	}

	/*
	 * Load the Maps JS API using Google's own inline bootstrap loader.
	 *
	 * NOT a plain <script src="…maps/api/js?loading=async"> tag. That URL returns
	 * a *loader* which injects main.js/places.js afterwards, and it does not
	 * define google.maps.importLibrary itself — verified: the returned bootstrap
	 * contains zero occurrences of the string. So the script's onload fires while
	 * importLibrary is still undefined, and calling it throws
	 * "google.maps.importLibrary is not a function".
	 *
	 * The official loader below defines importLibrary SYNCHRONOUSLY and queues
	 * calls until the library is ready, which removes the race entirely. Resolves
	 * once importLibrary exists; the caller awaits the library itself.
	 */
	function bootstrapMaps(key) {
		return new Promise(function (resolve, reject) {
			if (window.google && window.google.maps && window.google.maps.importLibrary) {
				resolve();
				return;
			}
			try {
				((g) => {
					var h,
						a,
						k,
						p = "The Google Maps JavaScript API",
						c = "google",
						l = "importLibrary",
						q = "__ib__",
						m = document,
						b = window;
					b = b[c] || (b[c] = {});
					var d = b.maps || (b.maps = {}),
						r = new Set(),
						e = new URLSearchParams(),
						u = () =>
							h ||
							(h = new Promise((f, n) => {
								a = m.createElement("script");
								e.set("libraries", [...r] + "");
								for (k in g)
									e.set(
										k.replace(/[A-Z]/g, (t) => "_" + t[0].toLowerCase()),
										g[k]
									);
								e.set("callback", c + ".maps." + q);
								a.src = "https://maps." + c + "apis.com/maps/api/js?" + e;
								d[q] = f;
								a.onerror = () => (h = n(Error(p + " could not load.")));
								a.nonce = (m.querySelector("script[nonce]") || {}).nonce || "";
								m.head.append(a);
							}));
					d[l]
						? console.warn(p + " only loads once. Ignoring:", g)
						: (d[l] = (f, ...n) => r.add(f) && u().then(() => d[l](f, ...n)));
				})({ key: key, v: "weekly" });
				resolve();
			} catch (err) {
				reject(err);
			}
		});
	}

	/* Console-only, and deliberately console.warn rather than console.error:
	   invisible to a customer, findable by whoever is configuring the site. */
	function warn(message, err) {
		if (window.console && console.warn) {
			console.warn("[fountain-move] " + message + ":", (err && err.message) || err || "");
		}
	}

	function onPlaceSelected(event) {
		var prediction = event.placePrediction;
		if (!prediction) return Promise.resolve();
		var place = prediction.toPlace();
		return place
			.fetchFields({
				fields: ["addressComponents", "formattedAddress", "location", "id"],
			})
			.then(function () {
				applyAddress(place);
			});
	}

	function applyAddress(place) {
		var parts = {};
		(place.addressComponents || []).forEach(function (component) {
			(component.types || []).forEach(function (type) {
				parts[type] = {
					long: component.longText,
					short: component.shortText,
				};
			});
		});

		var streetNumber = parts.street_number ? parts.street_number.long : "";
		var route = parts.route ? parts.route.long : "";

		setValue("address_line1", [streetNumber, route].filter(Boolean).join(" "));
		setValue("address_line2", parts.subpremise ? parts.subpremise.long : "");
		setValue(
			"city",
			(parts.locality && parts.locality.long) ||
				(parts.postal_town && parts.postal_town.long) ||
				(parts.sublocality && parts.sublocality.long) ||
				""
		);
		setValue(
			"state",
			parts.administrative_area_level_1 ? parts.administrative_area_level_1.short : ""
		);
		setValue("pincode", parts.postal_code ? parts.postal_code.long : "");

		form.dataset.placeId = place.id || "";
		form.dataset.formattedAddress = place.formattedAddress || "";
		if (place.location) {
			form.dataset.lat = place.location.lat();
			form.dataset.lng = place.location.lng();
		}
		form.dataset.autocompleted = "1";
	}

	function setValue(name, value) {
		var field = form.querySelector('[name="' + name + '"]');
		// Only overwrite when Google actually knows the answer — a rural address
		// that resolves without a street number must not blank what the customer
		// already typed.
		if (field && value) field.value = value;
	}

	// --------------------------------------------------------------- photos

	function wirePhotoInputs() {
		form.querySelectorAll("[data-photo]").forEach(function (input) {
			input.addEventListener("change", function () {
				var kind = input.dataset.photo;
				var file = input.files && input.files[0];
				if (!file) return;
				handlePhoto(kind, file, input);
			});
		});
		form.querySelectorAll("[data-clear]").forEach(function (button) {
			button.addEventListener("click", function () {
				var kind = button.dataset.clear;
				var input = form.querySelector('[data-photo="' + kind + '"]');
				if (input) input.value = "";
				preview(kind, null);
				status(kind, "", "");
			});
		});
	}

	function handlePhoto(kind, file, input) {
		status(kind, "Preparing photo…", "");
		state.uploading += 1;
		refreshSubmit();

		downscale(file)
			.then(function (blob) {
				preview(kind, blob);
				var limit = (BOOT.max_photo_mb || 10) * 1024 * 1024;
				if (blob.size > limit) {
					throw new Error("That photo is still larger than " + BOOT.max_photo_mb + " MB.");
				}
				status(kind, "Uploading…", "");
				return upload(kind, blob);
			})
			.then(function () {
				status(kind, "Photo added.", "done");
			})
			.catch(function (err) {
				status(kind, describeError(err), "error");
				if (input) input.value = "";
				preview(kind, null);
			})
			.finally(function () {
				state.uploading -= 1;
				refreshSubmit();
			});
	}

	/*
	 * Redraw the photo through a canvas at a sane size.
	 *
	 * Three things happen here at once, all of them wanted:
	 *   1. the file gets ~10x smaller, so the upload finishes on mobile data;
	 *   2. EXIF metadata (including GPS coordinates of the customer's home) is
	 *      dropped, because canvas re-encoding keeps only pixels;
	 *   3. iOS HEIC becomes JPEG, which the server accepts.
	 *
	 * If any of it fails we fall back to the original file and let the server
	 * decide — better a rejected upload with a clear message than a lost photo.
	 */
	function downscale(file) {
		return new Promise(function (resolve) {
			if (!window.createImageBitmap || !file.type.indexOf) {
				resolve(file);
				return;
			}
			createImageBitmap(file)
				.then(function (bitmap) {
					var scale = Math.min(1, MAX_EDGE / Math.max(bitmap.width, bitmap.height));
					var width = Math.round(bitmap.width * scale);
					var height = Math.round(bitmap.height * scale);

					var canvas = document.createElement("canvas");
					canvas.width = width;
					canvas.height = height;
					canvas.getContext("2d").drawImage(bitmap, 0, 0, width, height);
					bitmap.close && bitmap.close();

					canvas.toBlob(
						function (blob) {
							resolve(blob && blob.size < file.size ? blob : file);
						},
						"image/jpeg",
						JPEG_QUALITY
					);
				})
				.catch(function () {
					resolve(file);
				});
		});
	}

	function upload(kind, blob) {
		return ensureSession().then(function () {
			var body = new FormData();
			body.append("sid", state.sid);
			body.append("kind", kind);
			body.append("file", blob, "photo.jpg");
			return fetch(API + "upload_intake_photo", {
				method: "POST",
				// No Content-Type: the browser must set the multipart boundary.
				headers: withCsrf({}),
				body: body,
				credentials: "same-origin",
			}).then(readResponse);
		});
	}

	function preview(kind, blob) {
		var box = form.querySelector('[data-preview="' + kind + '"]');
		if (!box) return;
		var img = box.querySelector("img");
		if (!blob) {
			if (img.src.indexOf("blob:") === 0) URL.revokeObjectURL(img.src);
			img.removeAttribute("src");
			box.hidden = true;
			return;
		}
		if (img.src.indexOf("blob:") === 0) URL.revokeObjectURL(img.src);
		img.src = URL.createObjectURL(blob);
		box.hidden = false;
	}

	function status(kind, message, kindOfState) {
		var el = form.querySelector('[data-status="' + kind + '"]');
		if (!el) return;
		el.textContent = message;
		if (kindOfState) el.dataset.state = kindOfState;
		else el.removeAttribute("data-state");
	}

	// --------------------------------------------------------------- submit

	function onSubmit(event) {
		event.preventDefault();
		hideError();

		if (state.uploading > 0) {
			showError("Please wait for the photos to finish uploading.");
			return;
		}
		var invalid = firstInvalidField();
		if (invalid) {
			invalid.setAttribute("aria-invalid", "true");
			invalid.focus();
			showError(validationMessage(invalid));
			return;
		}

		state.submitting = true;
		refreshSubmit();

		ensureSession()
			.then(function () {
				return post("submit_intake", collect());
			})
			.then(function (result) {
				showSuccess(result.reference);
			})
			.catch(function (err) {
				showError(describeError(err));
				state.submitting = false;
				refreshSubmit();
			});
	}

	function collect() {
		var payload = { sid: state.sid };
		var data = new FormData(form);

		data.forEach(function (value, key) {
			payload[key] = value;
		});

		// Unchecked boxes are absent from FormData entirely, which the server
		// would read as "not answered" rather than "answered no".
		["water_access", "electricity_access", "contact_consent", "terms_accepted"].forEach(function (
			name
		) {
			payload[name] = form.querySelector('[name="' + name + '"]').checked ? 1 : 0;
		});

		if (form.dataset.autocompleted === "1") {
			payload.address_autocompleted = 1;
			payload.google_place_id = form.dataset.placeId || "";
			payload.formatted_address = form.dataset.formattedAddress || "";
			payload.latitude = form.dataset.lat || "";
			payload.longitude = form.dataset.lng || "";
		} else {
			payload.address_autocompleted = 0;
		}

		// The honeypot is submitted only when a bot filled it. Sending an empty
		// value on every request would trip the server's key-presence check for
		// every genuine customer.
		var honeypot = form.querySelector('[name="' + BOOT.honeypot_field + '"]');
		if (honeypot && !honeypot.value) delete payload[BOOT.honeypot_field];

		return payload;
	}

	function firstInvalidField() {
		var required = form.querySelectorAll("[required]");
		for (var i = 0; i < required.length; i++) {
			var field = required[i];
			field.removeAttribute("aria-invalid");
			if (field.type === "radio") {
				if (!form.querySelector('[name="' + field.name + '"]:checked')) return field;
			} else if (field.type === "checkbox") {
				if (!field.checked) return field;
			} else if (!field.value.trim() || !field.checkValidity()) {
				return field;
			}
		}
		return null;
	}

	function validationMessage(field) {
		if (field.name === "terms_accepted") {
			return "Please accept the Terms of Use and Privacy Policy to continue.";
		}
		if (field.name === "property_type") {
			return "Please tell us whether this is a residential or commercial property.";
		}
		if (field.type === "email") return "Please enter a valid email address.";
		var label = form.querySelector('label[for="' + field.id + '"]');
		var name = label ? label.textContent.replace("*", "").trim() : "one of the fields";
		return "Please fill in " + name + ".";
	}

	function showSuccess(reference) {
		form.hidden = true;
		document.getElementById("fm-reference").textContent = reference || "—";
		var box = document.getElementById("fm-success");
		box.hidden = false;
		box.scrollIntoView({ behavior: "smooth", block: "start" });
	}

	function refreshSubmit() {
		if (!submitBtn) return;
		var busy = state.submitting || state.uploading > 0;
		submitBtn.disabled = busy;
		submitBtn.textContent = state.submitting ? "Sending…" : "Send my request";
	}

	function showError(message) {
		errorBox.textContent = message;
		errorBox.hidden = false;
		errorBox.scrollIntoView({ behavior: "smooth", block: "center" });
	}

	function hideError() {
		errorBox.hidden = true;
		errorBox.textContent = "";
	}

	// ----------------------------------------------------------- transport

	function ensureSession() {
		if (state.sid) return Promise.resolve();
		// The session can be missing because Turnstile never rendered, or the
		// first begin_intake failed. Try once more so a transient network blip
		// on page load does not cost the customer their whole submission.
		return post("begin_intake", {
			turnstile_token: null,
			sid: null,
			ref: BOOT.invite_token,
		}).then(function (result) {
			state.sid = result.sid;
			state.verdict = result.verdict;
		});
	}

	function post(method, payload) {
		return fetch(API + method, {
			method: "POST",
			headers: withCsrf({ "Content-Type": "application/json", Accept: "application/json" }),
			credentials: "same-origin",
			body: JSON.stringify(payload),
		}).then(readResponse);
	}

	/*
	 * Add the CSRF header when — and only when — the session actually has a token.
	 *
	 * An anonymous customer has none, and frappe skips CSRF validation entirely
	 * for that case. A LOGGED-IN staff member previewing this page does have one,
	 * and frappe then enforces it: a POST without the header throws
	 * CSRFTokenError, which is HTTP 400 and broke the form outright for anyone
	 * signed in. Sending an empty header would be worse than sending none, so
	 * the key is omitted when there is nothing to send.
	 */
	function withCsrf(headers) {
		if (BOOT.csrf_token) headers["X-Frappe-CSRF-Token"] = BOOT.csrf_token;
		return headers;
	}

	function readResponse(response) {
		return response
			.text()
			.then(function (text) {
				var data = {};
				try {
					data = JSON.parse(text);
				} catch (e) {
					/* werkzeug's 413 is bare text, not JSON */
				}
				if (!response.ok) {
					var err = new Error(serverMessage(data) || "");
					err.status = response.status;
					throw err;
				}
				return data.message || data;
			});
	}

	/* Frappe puts authored frappe.throw() messages in _server_messages as a
	   JSON-encoded array of JSON-encoded objects. */
	function serverMessage(data) {
		try {
			var messages = JSON.parse(data._server_messages || "[]");
			for (var i = 0; i < messages.length; i++) {
				var parsed = JSON.parse(messages[i]);
				if (parsed && parsed.message) return stripTags(parsed.message);
			}
		} catch (e) {
			/* fall through */
		}
		return "";
	}

	function stripTags(html) {
		var div = document.createElement("div");
		div.innerHTML = html;
		return (div.textContent || "").trim();
	}

	/*
	 * Turn a failure into something a customer can act on.
	 *
	 * Status codes, verified against frappe:
	 *   429 — RateLimitExceededError. NOT 417.
	 *   417 — a generic ValidationError, which is what frappe.throw() produces;
	 *         the authored message is in _server_messages and is worth showing.
	 *   413 — werkzeug rejected the body before frappe saw it. Bare text.
	 */
	function describeError(err) {
		if (err && err.status === 429) {
			return (
				"We've had a lot of requests from this connection. Please wait a few " +
				"minutes and try again, or call us on " + contactPhone() + "."
			);
		}
		if (err && err.status === 413) {
			return "That photo is too large to upload. Please choose a smaller one.";
		}
		if (err && err.message) return err.message;
		return (
			"Something went wrong sending your request. Please try again, or call us on " +
			contactPhone() + "."
		);
	}

	/* Server-provided so the number lives in exactly one place. The literal is a
	   last resort for the case where the boot payload failed to render at all. */
	function contactPhone() {
		return BOOT.contact_phone || "(801) 837-2199";
	}

	function loadScript(src) {
		return new Promise(function (resolve, reject) {
			var script = document.createElement("script");
			script.src = src;
			script.async = true;
			script.onload = resolve;
			script.onerror = reject;
			document.head.appendChild(script);
		});
	}

	if (document.readyState === "loading") {
		document.addEventListener("DOMContentLoaded", init);
	} else {
		init();
	}
})();
