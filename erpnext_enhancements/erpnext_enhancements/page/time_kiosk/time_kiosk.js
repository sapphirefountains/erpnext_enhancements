frappe.pages['time-kiosk'].on_page_load = function(wrapper) {
	var page = frappe.ui.make_app_page({
		parent: wrapper,
		title: 'Time Kiosk',
		single_column: true
	});

	$(frappe.render_template('time_kiosk', {})).appendTo(page.main);

    // Load CSS
    frappe.require('/assets/erpnext_enhancements/css/time_kiosk.css');

    new TimeKioskController(page.main);
}

class TimeKioskController {
    constructor(wrapper) {
        this.wrapper = $(wrapper);
        this.currentStatus = null;
        this.timerInterval = null;

        this.bindEvents();
        this.loadProjects();
        this.checkStatus();
    }

    bindEvents() {
        this.wrapper.find('#btn-start').on('click', () => this.handleAction('Start'));
        this.wrapper.find('#btn-stop').on('click', () => this.handleAction('Stop'));
    }

    loadProjects() {
        frappe.call({
            method: 'erpnext_enhancements.erpnext_enhancements.api.time_kiosk.get_projects',
            callback: (r) => {
                if (r.message) {
                    const select = this.wrapper.find('#project-select');
                    select.empty();
                    select.append('<option value="">-- Select Project --</option>');
                    r.message.forEach(p => {
                        select.append(`<option value="${p.name}">${p.project_name || p.name}</option>`);
                    });
                }
            }
        });
    }

    checkStatus() {
        frappe.call({
            method: 'erpnext_enhancements.erpnext_enhancements.api.time_kiosk.get_current_status',
            callback: (r) => {
                this.updateUI(r.message);
                this.wrapper.find('#kiosk-loading').hide();
                this.wrapper.find('#kiosk-content').show();
            }
        });
    }

    updateUI(status) {
        this.currentStatus = status;
        const startBtn = this.wrapper.find('#btn-start');
        const stopBtn = this.wrapper.find('#btn-stop');
        const statusText = this.wrapper.find('#status-text');
        const timerText = this.wrapper.find('#timer-text');
        const projectSelect = this.wrapper.find('#project-select');
        const descriptionInput = this.wrapper.find('#description-input');
        const projectGroup = this.wrapper.find('#project-group');

        if (status) {
            // Clocked In
            startBtn.hide();
            stopBtn.show();
            projectGroup.hide(); // Hide project select when running
            descriptionInput.prop('disabled', true).val(status.description || '');

            statusText.text(`Clocked into: ${status.project_title}`);
            this.startTimer(status.start_time);
        } else {
            // Clocked Out
            startBtn.show();
            stopBtn.hide();
            projectGroup.show();
            descriptionInput.prop('disabled', false).val('');

            statusText.text('Not Working');
            timerText.text('--:--:--');
            this.stopTimer();
        }
    }

    startTimer(startTimeStr) {
        this.stopTimer();
        const startTime = new Date(startTimeStr).getTime();

        this.updateTimerText(startTime); // Immediate update

        this.timerInterval = setInterval(() => {
            this.updateTimerText(startTime);
        }, 1000);
    }

    stopTimer() {
        if (this.timerInterval) {
            clearInterval(this.timerInterval);
            this.timerInterval = null;
        }
    }

    updateTimerText(startTime) {
        const now = new Date().getTime();
        const diff = now - startTime;

        if (diff < 0) return; // Should not happen usually

        const hours = Math.floor(diff / (1000 * 60 * 60));
        const minutes = Math.floor((diff % (1000 * 60 * 60)) / (1000 * 60));
        const seconds = Math.floor((diff % (1000 * 60)) / 1000);

        const text = `${this.pad(hours)}:${this.pad(minutes)}:${this.pad(seconds)}`;
        this.wrapper.find('#timer-text').text(text);
    }

    pad(num) {
        return num.toString().padStart(2, '0');
    }

    handleAction(action) {
        const project = this.wrapper.find('#project-select').val();
        const description = this.wrapper.find('#description-input').val();

        if (action === 'Start' && !project) {
            frappe.msgprint('Please select a project.');
            return;
        }

        // Get Geolocation
        if (navigator.geolocation) {
             frappe.show_alert({message: 'Getting location...', indicator: 'orange'}, 2);
             navigator.geolocation.getCurrentPosition(
                (position) => {
                    this.callAPI(action, project, description, position.coords.latitude, position.coords.longitude);
                },
                (error) => {
                    console.warn("Geolocation error:", error);
                    frappe.msgprint("Could not get location. Proceeding anyway.");
                    this.callAPI(action, project, description, null, null);
                },
                { timeout: 10000, enableHighAccuracy: true }
            );
        } else {
             this.callAPI(action, project, description, null, null);
        }
    }

    callAPI(action, project, description, lat, lng) {
        frappe.call({
            method: 'erpnext_enhancements.erpnext_enhancements.api.time_kiosk.log_time',
            args: {
                project: project,
                action: action,
                description: description,
                lat: lat,
                lng: lng
            },
            freeze: true,
            freeze_message: action === 'Start' ? 'Clocking In...' : 'Clocking Out...',
            callback: (r) => {
                if (r.message && r.message.status === 'success') {
                    frappe.show_alert({message: r.message.message, indicator: 'green'});
                    this.checkStatus();
                }
            }
        });
    }
}
