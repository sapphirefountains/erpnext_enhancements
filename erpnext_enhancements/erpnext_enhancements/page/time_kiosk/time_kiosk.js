const TIME_KIOSK_TEMPLATE = `<div id="time-kiosk-app" class="time-kiosk-container" style="max-width: 600px; margin: 0 auto; padding: 20px;">
    <div class="text-center mb-5">
        <h1 id="tk-current-time" class="display-3 font-weight-bold">--:--:--</h1>
        <p id="tk-loading-msg" class="text-muted" style="display: none;">Loading...</p>
        <p id="tk-status-text" class="text-muted">Ready to Work</p>
    </div>

    <div class="card shadow-sm">
        <div class="card-body">
            <!-- Timer Display -->
            <div class="text-center mb-4">
                 <h2 id="tk-timer-display" class="display-4">--:--:--</h2>
                 <p id="tk-active-project-display" class="text-success font-weight-bold" style="display: none;">
                    <i class="fa fa-briefcase"></i> <span id="tk-active-project-name"></span>
                 </p>
            </div>

            <!-- Inputs (Hidden if Clocked In) -->
            <div id="tk-input-section">
                <div class="form-group">
                    <label>Project</label>
                    <div id="tk-project-wrapper"></div>
                </div>

                <div class="form-group">
                    <label>Note (Optional)</label>
                    <textarea id="tk-note-input" class="form-control" rows="3" placeholder="What are you working on?"></textarea>
                </div>
            </div>

            <!-- Read-only note if Clocked In -->
            <div id="tk-read-only-note-section" class="form-group text-center" style="display: none;">
                <p id="tk-read-only-note" class="text-muted font-italic"></p>
            </div>

            <!-- Actions -->
            <div class="mt-4">
                <button id="tk-btn-clock-in" class="btn btn-success btn-lg btn-block">
                    <i class="fa fa-play"></i> Clock In
                </button>

                <button id="tk-btn-clock-out" class="btn btn-danger btn-lg btn-block" style="display: none;">
                    <i class="fa fa-stop"></i> Clock Out
                </button>

                <a id="tk-btn-history" href="/app/job-interval" class="btn btn-secondary btn-lg btn-block mt-3">
                    <i class="fa fa-history"></i> View My History
                </a>
            </div>
        </div>
    </div>
</div>`;

// --- DEBUGGING HELPERS ---
function debug_log(msg) {
    try {
        console.log("[Time Kiosk] " + msg);

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
        // 1. Setup Page
        var page = frappe.ui.make_app_page({
            parent: wrapper,
            title: 'Time Kiosk',
            single_column: true
        });
        debug_log("Page structure created via make_app_page");

        // Load CSS manually to avoid frappe.require issues
        const cssId = 'time-kiosk-css';
        if (!document.getElementById(cssId)) {
            const head = document.getElementsByTagName('head')[0];
            const link = document.createElement('link');
            link.id = cssId;
            link.rel = 'stylesheet';
            link.type = 'text/css';
            link.href = '/assets/erpnext_enhancements/css/time-kiosk.css';
            link.media = 'all';
            head.appendChild(link);
            debug_log("CSS link appended to head");
        } else {
             debug_log("CSS already loaded");
        }

        // 2. Inject HTML
        $(page.main).html(TIME_KIOSK_TEMPLATE);
        debug_log("Template HTML injected into page.main");

        // 3. State & Elements
        const $currentTime = $('#tk-current-time');
        const $timerDisplay = $('#tk-timer-display');
        const $statusText = $('#tk-status-text');
        const $loadingMsg = $('#tk-loading-msg');

        const $inputSection = $('#tk-input-section');
        const $readOnlyNoteSection = $('#tk-read-only-note-section');

        // Link Field Wrapper
        const $projectWrapper = $('#tk-project-wrapper');
        let projectControl = null;

        const $noteInput = $('#tk-note-input');
        const $readOnlyNote = $('#tk-read-only-note');

        const $activeProjectDisplay = $('#tk-active-project-display');
        const $activeProjectName = $('#tk-active-project-name');

        const $btnClockIn = $('#tk-btn-clock-in');
        const $btnClockOut = $('#tk-btn-clock-out');
        const $btnHistory = $('#tk-btn-history');

        let kioskState = {
            status: null, // 'Open', 'Idle'
            currentInterval: null,
            loading: false
        };

        // 3.5 Initialize Link Field
        // Use frappe.ui.form.make_control to create a standard Link field
        try {
            projectControl = frappe.ui.form.make_control({
                parent: $projectWrapper,
                df: {
                    fieldtype: 'Link',
                    options: 'Project',
                    fieldname: 'project',
                    label: 'Project',
                    placeholder: 'Select Project...',
                    reqd: 1,
                    only_select: 1 // Attempt to render cleaner if possible, though Link ignores it
                },
                render_input: true
            });

            // Apply Custom Filter: Active Projects Only
            projectControl.get_query = function() {
                return {
                    filters: {
                        is_active: 'Yes' // Fallback handled by user preference, but usually 'Yes'
                    }
                };
            };

            // Bind change event to update local state if needed (not strictly needed as we read value on action)
            debug_log("Link field created successfully");
        } catch (e) {
            debug_log("Error creating Link field: " + e.message);
        }

        // 4. UI Helpers
        const setLoading = (isLoading) => {
            kioskState.loading = isLoading;
            if (isLoading) {
                $loadingMsg.show();
                $statusText.hide();
                $btnClockIn.prop('disabled', true);
                $btnClockOut.prop('disabled', true);
                if (projectControl) {
                    projectControl.df.read_only = 1;
                    projectControl.refresh();
                }
                $noteInput.prop('disabled', true);
            } else {
                $loadingMsg.hide();
                $statusText.show();
                $btnClockIn.prop('disabled', false);
                $btnClockOut.prop('disabled', false);
                if (projectControl) {
                    projectControl.df.read_only = 0;
                    projectControl.refresh();
                }
                $noteInput.prop('disabled', false);
            }
        };

        const renderState = () => {
            if (kioskState.status === 'Open') {
                // CLOCKED IN
                $statusText.text('Clocked In');

                $inputSection.hide();
                $btnClockIn.hide();

                $readOnlyNoteSection.show();
                $readOnlyNote.text(kioskState.currentInterval.description || 'No description provided.');

                $activeProjectDisplay.show();
                $activeProjectName.text(kioskState.currentInterval.project_title || kioskState.currentInterval.project);

                $btnClockOut.show();

                // Update clock immediately to avoid flicker
                updateClock();
            } else {
                // IDLE / READY
                $statusText.text('Ready to Work');

                $inputSection.show();
                $btnClockIn.show();

                $readOnlyNoteSection.hide();
                $activeProjectDisplay.hide();
                $btnClockOut.hide();
                $timerDisplay.text('--:--:--');
            }
        };

        const updateHistoryLink = (employeeId) => {
             if (employeeId) {
                 // Use frappe.utils.get_form_link if possible, or just build URL
                 const url = `/app/job-interval?employee=${encodeURIComponent(employeeId)}`;
                 $btnHistory.attr('href', url);
             }
        };

        // 5. Timer Logic
        const updateClock = () => {
            $currentTime.text(new Date().toLocaleTimeString());

            if (kioskState.status === 'Open' && kioskState.currentInterval && kioskState.currentInterval.start_time) {
                const start = new Date(kioskState.currentInterval.start_time).getTime();
                const now = new Date().getTime();
                const diff = now - start;

                if (diff >= 0) {
                    const hours = Math.floor(diff / (1000 * 60 * 60));
                    const minutes = Math.floor((diff % (1000 * 60 * 60)) / (1000 * 60));
                    const seconds = Math.floor((diff % (1000 * 60)) / 1000);
                    $timerDisplay.text(
                        `${hours.toString().padStart(2, '0')}:${minutes.toString().padStart(2, '0')}:${seconds.toString().padStart(2, '0')}`
                    );
                }
            } else if (kioskState.status !== 'Open') {
                 $timerDisplay.text('--:--:--');
            }
        };

        // Start Timer Interval
        setInterval(updateClock, 1000);
        updateClock(); // Initial call

        // 6. Data Fetching
        // Deprecated: fetchProjects (replaced by Link field)

        const fetchStatus = () => {
            setLoading(true);
            frappe.call({
                method: 'erpnext_enhancements.api.time_kiosk.get_current_status',
                callback: (r) => {
                    setLoading(false);
                    if (r.message) {
                        kioskState.status = 'Open';
                        kioskState.currentInterval = r.message;
                        debug_log("Status: Open (Interval ID: " + r.message.name + ")");

                        if (r.message.employee) {
                            updateHistoryLink(r.message.employee);
                        }
                    } else {
                        kioskState.status = 'Idle';
                        kioskState.currentInterval = null;
                        debug_log("Status: Idle");

                        if (r.message && r.message.employee) {
                             updateHistoryLink(r.message.employee);
                        }
                    }
                    renderState();
                },
                error: (r) => {
                    setLoading(false);
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
            // Get value from Link Control
            let selectedProject = null;
            if (projectControl) {
                selectedProject = projectControl.get_value();
            }
            const description = $noteInput.val();

            if (action === 'Start' && !selectedProject) {
                frappe.msgprint('Please select a project.');
                return;
            }

            setLoading(true);
            frappe.show_alert({message: action === 'Start' ? 'Clocking In...' : 'Clocking Out...', indicator: 'orange'});

            const loc = await getGeolocation();
            debug_log("Action: " + action + " Location: " + JSON.stringify(loc));

            frappe.call({
                method: 'erpnext_enhancements.api.time_kiosk.log_time',
                args: {
                    project: selectedProject,
                    action: action,
                    description: description,
                    lat: loc.lat,
                    lng: loc.lng
                },
                callback: (r) => {
                    if (r.message && r.message.status === 'success') {
                        frappe.show_alert({message: r.message.message, indicator: 'green'});
                        // Clear inputs
                        $noteInput.val('');
                        if (projectControl) projectControl.set_value('');
                        fetchStatus(); // Will update UI
                        debug_log("Action successful: " + r.message.message);
                    } else {
                        setLoading(false);
                        debug_log("Action failed or invalid response: " + JSON.stringify(r));
                    }
                },
                error: (r) => {
                    setLoading(false);
                    debug_log("Action API Error: " + JSON.stringify(r));
                }
            });
        };

        // 7. Event Listeners
        $btnClockIn.on('click', () => handleAction('Start'));
        $btnClockOut.on('click', () => handleAction('Stop'));

        // 8. Initial Load
        fetchStatus();

    } catch (e) {
        debug_log("Critical Error in init_time_kiosk: " + e.message);
        console.error(e);
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
