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
                    <label>Task (Optional)</label>
                    <div id="tk-task-wrapper"></div>
                </div>

                <div class="form-group">
                    <label>Category</label>
                    <select id="tk-category-input" class="form-control">
                        <option value="On-Site Labor">On-Site Labor</option>
                        <option value="Travel">Travel</option>
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
                <p id="tk-read-only-category" class="badge badge-info"></p>
            </div>

            <!-- Attachments (visible when clocked in) -->
            <div id="tk-attachments-section" style="display: none;">
                <hr class="mt-2 mb-3">
                <h6 class="mb-2" style="color: #6c757d;"><i class="fa fa-paperclip"></i> Attachments</h6>
                <div id="tk-attachment-list" class="mb-2" style="max-height: 130px; overflow-y: auto;"></div>
                <div class="row">
                    <div class="col-6">
                        <button id="tk-btn-add-attachment" class="btn btn-outline-primary btn-block btn-sm">
                            <i class="fa fa-paperclip"></i> Add Attachments
                        </button>
                    </div>
                    <div class="col-6">
                        <button id="tk-btn-take-picture" class="btn btn-outline-secondary btn-block btn-sm">
                            <i class="fa fa-camera"></i> Take Picture
                        </button>
                    </div>
                </div>
                <input type="file" id="tk-camera-input" accept="image/*" capture="environment" style="display: none;">
            </div>

            <!-- Actions -->
            <div class="mt-4">
                <button id="tk-btn-clock-in" class="btn btn-success btn-lg btn-block">
                    <i class="fa fa-play"></i> Clock In
                </button>

                <div id="tk-active-actions" style="display: none;">
                    <div class="row">
                        <div class="col-6">
                             <button id="tk-btn-pause" class="btn btn-warning btn-lg btn-block">
                                <i class="fa fa-pause"></i> Pause Break
                             </button>
                             <button id="tk-btn-resume" class="btn btn-info btn-lg btn-block" style="display: none;">
                                <i class="fa fa-play"></i> Resume Work
                             </button>
                        </div>
                        <div class="col-6">
                            <button id="tk-btn-switch" class="btn btn-secondary btn-lg btn-block">
                                <i class="fa fa-exchange"></i> Switch Task
                            </button>
                        </div>
                    </div>
                    <button id="tk-btn-clock-out" class="btn btn-danger btn-lg btn-block mt-3">
                        <i class="fa fa-stop"></i> Clock Out
                    </button>
                </div>

                <a id="tk-btn-history" href="/app/job-interval" class="btn btn-outline-secondary btn-lg btn-block mt-3">
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

const init_time_kiosk = function(wrapper) {
    debug_log("init_time_kiosk execution started");

    try {
        var page = frappe.ui.make_app_page({
            parent: wrapper,
            title: 'Time Kiosk',
            single_column: true
        });

        const cssId = 'time-kiosk-css';
        if (!document.getElementById(cssId)) {
            const head = document.getElementsByTagName('head')[0];
            const link = document.createElement('link');
            link.id = cssId;
            link.rel = 'stylesheet';
            link.type = 'text/css';
            link.href = '/assets/erpnext_enhancements/css/time-kiosk.bundle.css';
            link.media = 'all';
            head.appendChild(link);
        }

        $(page.main).html(TIME_KIOSK_TEMPLATE);

        const $currentTime = $('#tk-current-time');
        const $timerDisplay = $('#tk-timer-display');
        const $statusText = $('#tk-status-text');
        const $loadingMsg = $('#tk-loading-msg');
        const $inputSection = $('#tk-input-section');
        const $readOnlyNoteSection = $('#tk-read-only-note-section');
        const $projectWrapper = $('#tk-project-wrapper');
        const $taskWrapper = $('#tk-task-wrapper');
        const $categoryInput = $('#tk-category-input');
        const $noteInput = $('#tk-note-input');
        const $readOnlyNote = $('#tk-read-only-note');
        const $readOnlyCategory = $('#tk-read-only-category');
        const $activeProjectDisplay = $('#tk-active-project-display');
        const $activeProjectName = $('#tk-active-project-name');
        const $btnClockIn = $('#tk-btn-clock-in');
        const $activeActions = $('#tk-active-actions');
        const $btnPause = $('#tk-btn-pause');
        const $btnResume = $('#tk-btn-resume');
        const $btnSwitch = $('#tk-btn-switch');
        const $btnClockOut = $('#tk-btn-clock-out');
        const $btnHistory = $('#tk-btn-history');
        const $attachmentsSection = $('#tk-attachments-section');
        const $attachmentList = $('#tk-attachment-list');
        const $btnAddAttachment = $('#tk-btn-add-attachment');
        const $btnTakePicture = $('#tk-btn-take-picture');
        const $cameraInput = $('#tk-camera-input');

        let projectControl = null;
        let taskControl = null;
        let kioskState = {
            status: null, // 'Open', 'Paused', 'Idle'
            currentInterval: null,
            loading: false,
            isSwitching: false,
            attachments: []
        };

        let geoWorker = null;
        const initWorker = (employeeId) => {
            if (!window.Worker || geoWorker) return;
            geoWorker = new Worker('/assets/erpnext_enhancements/js/geo_worker.js');
            geoWorker.onmessage = (e) => {
                if (e.data.type === 'permission_denied') {
                    frappe.show_alert({message: "Location access required.", indicator: "orange"}, 5);
                }
            };
            geoWorker.postMessage({
                type: 'start',
                data: { employee: employeeId, user: frappe.session.user, csrf_token: frappe.csrf_token }
            });
        };

        const stopWorker = () => {
            if (geoWorker) {
                geoWorker.postMessage({ type: 'stop' });
                geoWorker.terminate();
                geoWorker = null;
            }
        };

        projectControl = frappe.ui.form.make_control({
            parent: $projectWrapper,
            df: { fieldtype: 'Link', options: 'Project', fieldname: 'project', label: 'Project', reqd: 1 },
            render_input: true
        });
        projectControl.get_query = () => ({ filters: { is_active: 'Yes' } });

        taskControl = frappe.ui.form.make_control({
            parent: $taskWrapper,
            df: { fieldtype: 'Link', options: 'Task', fieldname: 'task', label: 'Task', reqd: 0 },
            render_input: true
        });
        taskControl.get_query = () => ({ filters: { project: projectControl.get_value() } });

        projectControl.$input.on('change', () => {
            taskControl.set_value('');
            const cp = projectControl.get_value();
            taskControl.get_query = () => ({ filters: { project: cp } });
        });

        const renderAttachmentList = () => {
            $attachmentList.empty();
            if (!kioskState.attachments.length) {
                $attachmentList.html('<p class="text-muted small mb-0">No attachments yet.</p>');
                return;
            }
            kioskState.attachments.forEach(att => {
                const fname = frappe.utils.escape_html(att.file_name || 'Attachment');
                const ext = fname.split('.').pop().toLowerCase();
                const icon = ['jpg','jpeg','png','gif','webp'].includes(ext) ? 'fa-file-image-o'
                           : ext === 'pdf' ? 'fa-file-pdf-o' : 'fa-file-o';
                $attachmentList.append(
                    `<div class="d-flex align-items-center mb-1">
                        <i class="fa ${icon} mr-2 text-muted"></i>
                        <small class="text-truncate" style="max-width: 240px;">${fname}</small>
                    </div>`
                );
            });
        };

        const linkFileToRecords = (fileName) => {
            frappe.call({
                method: 'erpnext_enhancements.api.time_kiosk.link_attachment',
                args: {
                    file_name: fileName,
                    project: kioskState.currentInterval.project,
                    task: kioskState.currentInterval.task || null
                },
                callback: (r) => {
                    if (r.message && r.message.status === 'success') {
                        kioskState.attachments.push({
                            file_name: r.message.file_name,
                            file_url: r.message.file_url
                        });
                        renderAttachmentList();
                        frappe.show_alert({ message: 'Attachment added.', indicator: 'green' });
                    }
                }
            });
        };

        const uploadCameraFile = (file) => {
            frappe.show_alert({ message: 'Uploading…', indicator: 'blue' });
            const formData = new FormData();
            formData.append('file', file, file.name);
            formData.append('is_private', '0');
            formData.append('doctype', 'Job Interval');
            formData.append('docname', kioskState.currentInterval.name);
            formData.append('folder', 'Home/Attachments');

            fetch('/api/method/upload_file', {
                method: 'POST',
                headers: { 'X-Frappe-CSRF-Token': frappe.csrf_token },
                body: formData
            })
            .then(r => r.json())
            .then(data => {
                if (data.message && data.message.name) {
                    linkFileToRecords(data.message.name);
                } else {
                    frappe.show_alert({ message: 'Upload failed.', indicator: 'red' });
                }
            })
            .catch(() => frappe.show_alert({ message: 'Upload failed.', indicator: 'red' }));
        };

        const setLoading = (isLoading) => {
            kioskState.loading = isLoading;
            if (isLoading) {
                $loadingMsg.show(); $statusText.hide();
                $btnClockIn.prop('disabled', true);
                $btnPause.prop('disabled', true); $btnResume.prop('disabled', true);
                $btnSwitch.prop('disabled', true); $btnClockOut.prop('disabled', true);
            } else {
                $loadingMsg.hide(); $statusText.show();
                $btnClockIn.prop('disabled', false);
                $btnPause.prop('disabled', false); $btnResume.prop('disabled', false);
                $btnSwitch.prop('disabled', false); $btnClockOut.prop('disabled', false);
            }
        };

        const renderState = () => {
            if (kioskState.status === 'Open' || kioskState.status === 'Paused') {
                $statusText.text(kioskState.status === 'Open' ? 'Clocked In' : 'On Break (Paused)');
                
                if (kioskState.isSwitching) {
                    $inputSection.show();
                    $readOnlyNoteSection.hide();
                    $btnClockIn.hide();
                    $activeActions.show();
                    $btnSwitch.html('<i class="fa fa-check"></i> Confirm & Switch').addClass('btn-primary').removeClass('btn-secondary');
                } else {
                    $inputSection.hide();
                    $readOnlyNoteSection.show();
                    $readOnlyNote.text(kioskState.currentInterval.description || 'No description provided.');
                    $readOnlyCategory.text(kioskState.currentInterval.time_category || 'On-Site Labor');
                    $btnClockIn.hide();
                    $activeActions.show();
                    $btnSwitch.html('<i class="fa fa-exchange"></i> Switch Task').addClass('btn-secondary').removeClass('btn-primary');
                }

                $btnPause.toggle(kioskState.status === 'Open');
                $btnResume.toggle(kioskState.status === 'Paused');

                $attachmentsSection.show();
                renderAttachmentList();

                $activeProjectDisplay.show();
                let displayTitle = kioskState.currentInterval.project_title || kioskState.currentInterval.project;
                if (kioskState.currentInterval.task) {
                     displayTitle += ' - ' + (kioskState.currentInterval.task_title || kioskState.currentInterval.task);
                }
                $activeProjectName.text(displayTitle);

                if (kioskState.currentInterval.employee) {
                    initWorker(kioskState.currentInterval.employee);
                    localStorage.setItem('tk_is_clocked_in', 'true');
                    localStorage.setItem('tk_employee', kioskState.currentInterval.employee);
                }
            } else {
                $statusText.text('Ready to Work');
                $inputSection.show();
                $btnClockIn.show();
                $activeActions.hide();
                $readOnlyNoteSection.hide();
                $activeProjectDisplay.hide();
                $attachmentsSection.hide();
                $attachmentList.empty();
                kioskState.attachments = [];
                $timerDisplay.text('--:--:--');
                stopWorker();
                localStorage.removeItem('tk_is_clocked_in');
                localStorage.removeItem('tk_employee');
            }
        };

        const updateClock = () => {
            $currentTime.text(new Date().toLocaleTimeString());
            if ((kioskState.status === 'Open' || kioskState.status === 'Paused') && kioskState.currentInterval && kioskState.currentInterval.start_time) {
                const start = new Date(kioskState.currentInterval.start_time).getTime();
                const now = (kioskState.status === 'Paused' && kioskState.currentInterval.last_pause_time) 
                            ? new Date(kioskState.currentInterval.last_pause_time).getTime() 
                            : new Date().getTime();
                
                const paused_ms = (kioskState.currentInterval.total_paused_seconds || 0) * 1000;
                const diff = now - start - paused_ms;

                if (diff >= 0) {
                    const hours = Math.floor(diff / 3600000);
                    const minutes = Math.floor((diff % 3600000) / 60000);
                    const seconds = Math.floor((diff % 60000) / 1000);
                    $timerDisplay.text(`${hours.toString().padStart(2, '0')}:${minutes.toString().padStart(2, '0')}:${seconds.toString().padStart(2, '0')}`);
                }
            }
        };
        setInterval(updateClock, 1000);

        const fetchStatus = () => {
            setLoading(true);
            frappe.call({
                method: 'erpnext_enhancements.api.time_kiosk.get_current_status',
                callback: (r) => {
                    setLoading(false);
                    if (r.message && r.message.name) {
                        kioskState.status = r.message.status;
                        kioskState.currentInterval = r.message;
                        kioskState.attachments = r.message.attachments || [];
                        kioskState.isSwitching = false;
                    } else {
                        kioskState.status = 'Idle';
                        kioskState.currentInterval = null;
                        kioskState.attachments = [];
                    }
                    renderState();
                }
            });
        };

        const handleAction = async (action) => {
            const project = projectControl.get_value();
            const task = taskControl.get_value();
            const description = $noteInput.val();
            const category = $categoryInput.val();

            if ((action === 'Start' || action === 'Switch') && !project) {
                frappe.msgprint('Please select a project.');
                return;
            }

            setLoading(true);
            frappe.call({
                method: 'erpnext_enhancements.api.time_kiosk.log_time',
                args: { project, task, action, description, time_category: category },
                callback: (r) => {
                    if (r.message && r.message.status === 'success') {
                        frappe.show_alert({message: r.message.message, indicator: 'green'});
                        $noteInput.val('');
                        fetchStatus();
                    } else {
                        setLoading(false);
                    }
                }
            });
        };

        const promptIfNoAttachments = (message, onNoAttachments) => {
            if (kioskState.attachments.length === 0) {
                frappe.confirm(
                    message,
                    () => frappe.show_alert({ message: 'Add your attachments first.', indicator: 'blue' }),
                    onNoAttachments
                );
            } else {
                onNoAttachments();
            }
        };

        $btnClockIn.on('click', () => handleAction('Start'));

        $btnClockOut.on('click', () => {
            promptIfNoAttachments(
                'Do you have any attachments to add before clocking out?',
                () => handleAction('Stop')
            );
        });

        $btnPause.on('click', () => handleAction('Pause'));
        $btnResume.on('click', () => handleAction('Resume'));

        $btnSwitch.on('click', () => {
            if (!kioskState.isSwitching) {
                promptIfNoAttachments(
                    'Do you have any attachments to add before switching tasks?',
                    () => { kioskState.isSwitching = true; renderState(); }
                );
            } else {
                handleAction('Switch');
            }
        });

        $btnAddAttachment.on('click', () => {
            if (!kioskState.currentInterval) return;
            new frappe.ui.FileUploader({
                doctype: 'Job Interval',
                docname: kioskState.currentInterval.name,
                allow_multiple: true,
                on_success: (file_doc) => linkFileToRecords(file_doc.name)
            });
        });

        $btnTakePicture.on('click', () => {
            if (!kioskState.currentInterval) return;
            $cameraInput.trigger('click');
        });

        $cameraInput.on('change', function() {
            const files = Array.from(this.files);
            if (!files.length) return;
            files.forEach(uploadCameraFile);
            this.value = '';
        });

        fetchStatus();
    } catch (e) {
        debug_log("Critical Error: " + e.message);
    }
};

if (frappe.pages['time-kiosk']) frappe.pages['time-kiosk'].on_page_load = init_time_kiosk;
else frappe.pages['time-kiosk'] = { on_page_load: init_time_kiosk };
