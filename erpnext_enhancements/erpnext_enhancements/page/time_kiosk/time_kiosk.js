const TIME_KIOSK_TEMPLATE = `<div id="time-kiosk-app" class="time-kiosk-container" style="max-width: 600px; margin: 0 auto; padding: 20px;">
    <div class="text-center mb-5">
        <h1 id="tk-current-time" class="display-1 font-weight-bold">--:--:--</h1>
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
                    <select id="tk-project-select" class="form-control">
                        <option value="">-- Select Project --</option>
                    </select>
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
            </div>
        </div>
    </div>
</div>`;

// --- DEBUGGING HELPERS ---
function debug_log(msg) {
    try {
        console.log("[Time Kiosk] " + msg);

        // Check if document.body exists to prevent crash during early load
        if (!document.body) {
            console.log("[Time Kiosk] (Body not ready) Visual log skipped.");
            return;
        }

        // Visual Log
        let logContainer = document.getElementById('debug-log');
        if (!logContainer) {
            logContainer = document.createElement('div');
            logContainer.id = 'debug-log';
            Object.assign(logContainer.style, {
                position: 'fixed', bottom: '0', left: '0', width: '100%', height: '200px',
                overflowY: 'scroll', backgroundColor: 'rgba(0,0,0,0.9)', color: '#0f0',
                zIndex: '99999', padding: '10px', fontSize: '12px', fontFamily: 'monospace',
                pointerEvents: 'none' // Allow clicks to pass through
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
        const $projectSelect = $('#tk-project-select');
        const $noteInput = $('#tk-note-input');
        const $readOnlyNote = $('#tk-read-only-note');

        const $activeProjectDisplay = $('#tk-active-project-display');
        const $activeProjectName = $('#tk-active-project-name');

        const $btnClockIn = $('#tk-btn-clock-in');
        const $btnClockOut = $('#tk-btn-clock-out');

        let kioskState = {
            status: null, // 'Open', 'Idle'
            currentInterval: null,
            projects: [],
            loading: false
        };

        // 4. UI Helpers
        const setLoading = (isLoading) => {
            kioskState.loading = isLoading;
            if (isLoading) {
                $loadingMsg.show();
                $statusText.hide();
                $btnClockIn.prop('disabled', true);
                $btnClockOut.prop('disabled', true);
                $projectSelect.prop('disabled', true);
                $noteInput.prop('disabled', true);
            } else {
                $loadingMsg.hide();
                $statusText.show();
                $btnClockIn.prop('disabled', false);
                $btnClockOut.prop('disabled', false);
                $projectSelect.prop('disabled', false);
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

        const renderProjects = () => {
            $projectSelect.empty();
            $projectSelect.append('<option value="">-- Select Project --</option>');
            kioskState.projects.forEach(p => {
                const name = p.project_name || p.name;
                const $opt = $('<option></option>').val(p.name).text(name);
                $projectSelect.append($opt);
            });
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
        const fetchProjects = () => {
            debug_log("Fetching projects...");
            frappe.call({
                method: 'erpnext_enhancements.api.time_kiosk.get_projects',
                callback: (r) => {
                    if (r.message) {
                        kioskState.projects = r.message;
                        debug_log("Projects fetched: " + kioskState.projects.length);
                        renderProjects();
                    } else {
                        debug_log("No projects returned or empty message");
                    }
                },
                error: (r) => { debug_log("Error fetching projects: " + JSON.stringify(r)); }
            });
        };

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
                    } else {
                        kioskState.status = 'Idle';
                        kioskState.currentInterval = null;
                        debug_log("Status: Idle");
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
            const selectedProject = $projectSelect.val();
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
        fetchProjects();
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
