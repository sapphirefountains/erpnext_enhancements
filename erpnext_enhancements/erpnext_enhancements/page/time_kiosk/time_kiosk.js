const TIME_KIOSK_TEMPLATE = `<div id="time-kiosk-app" class="time-kiosk-container" style="max-width: 600px; margin: 0 auto; padding: 20px;">
    <div class="text-center mb-5">
        <h1 class="display-1 font-weight-bold">{{ currentTime }}</h1>
        <p class="text-muted" v-if="loading">Loading...</p>
        <p class="text-muted" v-else>
            {{ status === 'Open' ? 'Clocked In' : 'Ready to Work' }}
        </p>
    </div>

    <div class="card shadow-sm">
        <div class="card-body">
            <!-- Timer Display -->
            <div class="text-center mb-4">
                 <h2 class="display-4">{{ timerDisplay }}</h2>
                 <p v-if="status === 'Open'" class="text-success font-weight-bold">
                    <i class="fa fa-briefcase"></i> {{ currentInterval ? currentInterval.project_title : '' }}
                 </p>
            </div>

            <!-- Inputs (Hidden if Clocked In) -->
            <div v-if="status !== 'Open'" class="form-group">
                <label>Project</label>
                <select class="form-control" v-model="selectedProject" :disabled="loading">
                    <option value="">-- Select Project --</option>
                    <option v-for="p in projects" :key="p.name" :value="p.name">
                        {{ p.project_name || p.name }}
                    </option>
                </select>
            </div>

            <div v-if="status !== 'Open'" class="form-group">
                <label>Note (Optional)</label>
                <textarea class="form-control" v-model="description" rows="3" :disabled="loading" placeholder="What are you working on?"></textarea>
            </div>

            <!-- Read-only note if Clocked In -->
            <div v-if="status === 'Open'" class="form-group text-center">
                <p class="text-muted font-italic">{{ description || 'No description provided.' }}</p>
            </div>

            <!-- Actions -->
            <div class="mt-4">
                <button v-if="status !== 'Open'"
                    @click="handleAction('Start')"
                    class="btn btn-success btn-lg btn-block"
                    :disabled="loading || !selectedProject">
                    <i class="fa fa-play"></i> Clock In
                </button>

                <button v-else
                    @click="handleAction('Stop')"
                    class="btn btn-danger btn-lg btn-block"
                    :disabled="loading">
                    <i class="fa fa-stop"></i> Clock Out
                </button>
            </div>
        </div>
    </div>
</div>`;

// --- DEBUGGING HELPERS ---
function debug_log(msg) {
    try {
        console.log("[Time Kiosk] " + msg);

        // Visual Log
        let logContainer = document.getElementById('debug-log');
        if (!logContainer) {
            logContainer = document.createElement('div');
            logContainer.id = 'debug-log';
            Object.assign(logContainer.style, {
                position: 'fixed', bottom: '0', left: '0', width: '100%', height: '200px',
                overflowY: 'scroll', backgroundColor: 'rgba(0,0,0,0.9)', color: '#0f0',
                zIndex: '99999', padding: '10px', fontSize: '12px', fontFamily: 'monospace'
            });
            document.body.appendChild(logContainer);
        }
        const entry = document.createElement('div');
        entry.innerText = new Date().toLocaleTimeString() + ': ' + msg;
        logContainer.prepend(entry);

        // Server Log
        if (window.frappe && frappe.call) {
             frappe.call({
                method: 'erpnext_enhancements.api.logger.log_client_error',
                args: { error_message: "[Time Kiosk Client Log] " + msg },
                callback: function() {},
                freeze: false
            });
        }
    } catch (e) {
        console.error("Logging failed", e);
    }
}

debug_log("Script Loaded. Window URL: " + window.location.href);

const init_time_kiosk = function(wrapper) {
    debug_log("init_time_kiosk execution started");

    try {
        var page = frappe.ui.make_app_page({
            parent: wrapper,
            title: 'Time Kiosk',
            single_column: true
        });
        debug_log("Page structure created via make_app_page");

        // Load CSS
        frappe.require('/assets/erpnext_enhancements/css/time_kiosk.css');
        debug_log("CSS required");

        // Load Vue 3 global script
        const vueUrl = '/assets/erpnext_enhancements/js/vue.global.js';
        debug_log("Requesting Vue from: " + vueUrl);

        frappe.require(vueUrl, function() {
            debug_log("frappe.require callback received for Vue");

            try {
                // Idempotency check: prevent duplicate initialization
                if (wrapper.vue_app_mounted) {
                    debug_log("Wrapper already has vue_app_mounted=true, skipping.");
                    return;
                }

                if (!window.Vue) {
                    throw new Error("window.Vue is NOT defined after require callback!");
                }
                debug_log("Vue available. Version: " + window.Vue.version);

                // Explicitly render and inject the template
                $(page.main).html(TIME_KIOSK_TEMPLATE);
                debug_log("Template HTML injected into page.main");

                const { createApp, ref, onMounted, computed } = window.Vue;

                const TimeKioskApp = {
                    setup() {
                        debug_log("Vue setup() running");
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
                            debug_log("Vue Component Mounted");
                            fetchProjects();
                            fetchStatus();
                            setInterval(updateClock, 1000);
                        });

                        const fetchProjects = () => {
                            debug_log("Fetching projects...");
                            frappe.call({
                                method: 'erpnext_enhancements.api.time_kiosk.get_projects',
                                callback: (r) => {
                                    if (r.message) {
                                        projects.value = r.message;
                                        debug_log("Projects fetched: " + projects.value.length);
                                    } else {
                                        debug_log("No projects returned or empty message");
                                    }
                                },
                                error: (r) => { debug_log("Error fetching projects: " + JSON.stringify(r)); }
                            });
                        };

                        const fetchStatus = () => {
                            loading.value = true;
                            frappe.call({
                                method: 'erpnext_enhancements.api.time_kiosk.get_current_status',
                                callback: (r) => {
                                    loading.value = false;
                                    if (r.message) {
                                        status.value = 'Open';
                                        currentInterval.value = r.message;
                                        selectedProject.value = r.message.project;
                                        description.value = r.message.description || '';
                                        debug_log("Status: Open (Interval ID: " + r.message.name + ")");
                                    } else {
                                        status.value = 'Idle';
                                        currentInterval.value = null;
                                        debug_log("Status: Idle");
                                    }
                                },
                                error: (r) => {
                                    loading.value = false;
                                    debug_log("Error fetching status: " + JSON.stringify(r));
                                }
                            });
                        };

                        const getGeolocation = () => {
                            return new Promise((resolve, reject) => {
                                if (!navigator.geolocation) {
                                    debug_log("Geolocation not supported");
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
                                            debug_log("Geolocation error: " + error.message);
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
                            frappe.show_alert({message: action === 'Start' ? 'Clocking In...' : 'Clocking Out...', indicator: 'orange'});

                            const loc = await getGeolocation();
                            debug_log("Action: " + action + " Location: " + JSON.stringify(loc));

                            frappe.call({
                                method: 'erpnext_enhancements.api.time_kiosk.log_time',
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
                                        debug_log("Action successful: " + r.message.message);
                                    } else {
                                        loading.value = false;
                                        debug_log("Action failed or invalid response: " + JSON.stringify(r));
                                    }
                                },
                                error: (r) => {
                                    loading.value = false;
                                    debug_log("Action API Error: " + JSON.stringify(r));
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
                };

                debug_log("Mounting Vue App...");
                const app = createApp(TimeKioskApp);
                app.config.errorHandler = (err, vm, info) => {
                    console.error("Vue Error:", err);
                    debug_log("Vue Error: " + err + " Info: " + info);
                };

                app.mount('#time-kiosk-app');
                wrapper.vue_app_mounted = true;
                debug_log("Vue App Mounted Successfully");

            } catch (e) {
                console.error("Time Kiosk Vue Error:", e);
                debug_log("Error inside require callback: " + e.message + "\n" + e.stack);
                frappe.msgprint("Error initializing Time Kiosk: " + e.message);
            }
        });
    } catch (outerErr) {
        debug_log("Critical Error in init_time_kiosk: " + outerErr.message);
        console.error(outerErr);
    }
};

debug_log("Registering page handlers");

// Register for standard route key (slugified)
if (frappe.pages['time-kiosk']) {
    debug_log("Hooking into existing frappe.pages['time-kiosk']");
    frappe.pages['time-kiosk'].on_page_load = init_time_kiosk;
} else {
    debug_log("Creating new frappe.pages['time-kiosk'] entry");
    frappe.pages['time-kiosk'] = { on_page_load: init_time_kiosk };
}

// Fallback: Register for underscore key just in case
if (frappe.pages['time_kiosk']) {
    debug_log("Hooking into existing frappe.pages['time_kiosk']");
    frappe.pages['time_kiosk'].on_page_load = init_time_kiosk;
} else {
    debug_log("Creating new frappe.pages['time_kiosk'] entry");
    frappe.pages['time_kiosk'] = { on_page_load: init_time_kiosk };
}

// Fallback: Register for "Time Kiosk" (spaces) as seen in screenshot
if (frappe.pages['Time Kiosk']) {
    debug_log("Hooking into existing frappe.pages['Time Kiosk']");
    frappe.pages['Time Kiosk'].on_page_load = init_time_kiosk;
} else {
    debug_log("Creating new frappe.pages['Time Kiosk'] entry");
    frappe.pages['Time Kiosk'] = { on_page_load: init_time_kiosk };
}

// Fallback: Register for "Time%20Kiosk" (encoded) just in case
if (frappe.pages['Time%20Kiosk']) {
    debug_log("Hooking into existing frappe.pages['Time%20Kiosk']");
    frappe.pages['Time%20Kiosk'].on_page_load = init_time_kiosk;
} else {
    debug_log("Creating new frappe.pages['Time%20Kiosk'] entry");
    frappe.pages['Time%20Kiosk'] = { on_page_load: init_time_kiosk };
}
