frappe.pages['time-kiosk'].on_page_load = function(wrapper) {
	var page = frappe.ui.make_app_page({
		parent: wrapper,
		title: 'Time Kiosk',
		single_column: true
	});

    // Load CSS
    frappe.require('/assets/erpnext_enhancements/css/time_kiosk.css');

	// Load Vue 3 global script
	frappe.require('/assets/erpnext_enhancements/js/vue.global.js', function() {
		try {
            // Explicitly render and inject the template
            $(page.main).html(frappe.render_template("time_kiosk", {}));

			const { createApp, ref, onMounted, computed } = window.Vue;

			const TimeKioskApp = {
				setup() {
					const status = ref(null); // 'Open' or null/other
					const currentInterval = ref(null); // The actual interval object
					const projects = ref([]);
					const selectedProject = ref('');
					const description = ref('');
					const loading = ref(false);
					const currentTime = ref(new Date().toLocaleTimeString());
					const timerDisplay = ref('--:--:--');

					// Clock logic
					const updateClock = () => {
						currentTime.value = new Date().toLocaleTimeString();
						if (currentInterval.value && currentInterval.value.start_time) {
							const start = new Date(currentInterval.value.start_time).getTime();
							const now = new Date().getTime();
							const diff = now - start;
							if (diff >= 0) {
								const hours = Math.floor(diff / (1000 * 60 * 60));
								const minutes = Math.floor((diff % (1000 * 60 * 60)) / (1000 * 60));
								const seconds = Math.floor((diff % (1000 * 60)) / 1000);
								timerDisplay.value = `${hours.toString().padStart(2, '0')}:${minutes.toString().padStart(2, '0')}:${seconds.toString().padStart(2, '0')}`;
							}
						} else {
							timerDisplay.value = '--:--:--';
						}
					};

					onMounted(() => {
						fetchProjects();
						fetchStatus();
						setInterval(updateClock, 1000);
					});

					const fetchProjects = () => {
						frappe.call({
							method: 'erpnext_enhancements.erpnext_enhancements.api.time_kiosk.get_projects',
							callback: (r) => {
								if (r.message) {
									projects.value = r.message;
								}
							}
						});
					};

					const fetchStatus = () => {
						loading.value = true;
						frappe.call({
							method: 'erpnext_enhancements.erpnext_enhancements.api.time_kiosk.get_current_status',
							callback: (r) => {
								loading.value = false;
								if (r.message) {
									status.value = 'Open';
									currentInterval.value = r.message;
									selectedProject.value = r.message.project;
									description.value = r.message.description || '';
								} else {
									status.value = 'Idle';
									currentInterval.value = null;
								}
							}
						});
					};

					const getGeolocation = () => {
						return new Promise((resolve, reject) => {
							if (!navigator.geolocation) {
								resolve({ lat: null, lng: null });
							} else {
								navigator.geolocation.getCurrentPosition(
									(position) => {
										resolve({
											lat: position.coords.latitude,
											lng: position.coords.longitude
										});
									},
									(error) => {
										console.warn("Geolocation error", error);
										// Proceed without location if error
										resolve({ lat: null, lng: null });
									},
									{ timeout: 10000, enableHighAccuracy: true }
								);
							}
						});
					};

					const handleAction = async (action) => {
						if (action === 'Start' && !selectedProject.value) {
							frappe.msgprint('Please select a project.');
							return;
						}

						loading.value = true;
                        // Feedback
                        frappe.show_alert({message: action === 'Start' ? 'Clocking In...' : 'Clocking Out...', indicator: 'orange'});

						const loc = await getGeolocation();

						frappe.call({
							method: 'erpnext_enhancements.erpnext_enhancements.api.time_kiosk.log_time',
							args: {
								project: selectedProject.value,
								action: action,
								description: description.value,
								lat: loc.lat,
								lng: loc.lng
							},
							callback: (r) => {
								if (r.message && r.message.status === 'success') {
									frappe.show_alert({message: r.message.message, indicator: 'green'});
									fetchStatus();
								} else {
                                    loading.value = false;
                                }
							},
                            error: (r) => {
                                loading.value = false;
                            }
						});
					};

					return {
						status,
						projects,
						selectedProject,
						description,
						loading,
						currentTime,
						timerDisplay,
						currentInterval,
						handleAction
					};
				}
                // No template property - Vue uses the DOM content
			};

			createApp(TimeKioskApp).mount('#time-kiosk-app');

		} catch (e) {
			console.error("Time Kiosk Vue Error:", e);
            frappe.msgprint("Error initializing Time Kiosk: " + e.message);
		}
	});
};
