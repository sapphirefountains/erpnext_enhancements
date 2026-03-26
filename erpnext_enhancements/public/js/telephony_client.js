frappe.provide('erpnext_enhancements.telephony');

erpnext_enhancements.telephony = {
    device: null,
    identity: 'client:nikolas_erpnext',
    is_ready: false,

    init: function() {
        this.load_twilio_script()
            .then(() => this.request_permissions())
            .then(() => this.fetch_token())
            .then((token) => this.setup_device(token))
            .catch(err => {
                console.error("Telephony Initialization Failed:", err);
            });
    },

    load_twilio_script: function() {
        return new Promise((resolve, reject) => {
            if (window.Twilio && window.Twilio.Device) {
                return resolve();
            }
            const script = document.createElement('script');
            script.src = 'https://sdk.twilio.com/js/voice/v2.x/twilio.min.js';
            script.onload = resolve;
            script.onerror = () => reject(new Error('Failed to load Twilio SDK'));
            document.head.appendChild(script);
        });
    },

    request_permissions: function() {
        return navigator.mediaDevices.getUserMedia({ audio: true })
            .then((stream) => {
                console.log('Microphone permissions granted');
                stream.getTracks().forEach(track => track.stop());
            })
            .catch(err => {
                frappe.msgprint({
                    title: __('Microphone Access Denied'),
                    indicator: 'red',
                    message: __('Please grant microphone permissions to use the softphone.')
                });
                throw err;
            });
    },

    fetch_token: function() {
        return new Promise((resolve, reject) => {
            frappe.call({
                method: 'erpnext_enhancements.api.telephony.get_softphone_token',
                callback: function(r) {
                    if (r.message) {
                        resolve(r.message);
                    } else {
                        reject(new Error('No token returned from server'));
                    }
                },
                error: function(err) {
                    reject(err);
                }
            });
        });
    },

    setup_device: function(token) {
        this.device = new Twilio.Device(token, {
            codecPreferences: ['opus', 'pcmu'],
            fakeLocalDTMF: true,
            enableRingingState: true
        });

        this.device.on('ready', (device) => {
            console.log('Twilio Device Ready');
            this.is_ready = true;
        });

        // Listen for registered event
        this.device.on('registered', () => {
            console.log('Twilio Device Registered to handle incoming calls');
        });

        // Required for Twilio Voice v2.x to receive incoming connections
        this.device.register();

        this.device.on('error', (error) => {
            console.error('Twilio Device Error:', error);
            frappe.show_alert({message: `Twilio Error: ${error.message}`, indicator: 'red'});
        });

        this.device.on('incoming', (call) => {
            this.handle_incoming_call(call);
        });
    },

    handle_incoming_call: function(call) {
        const caller_id = call.parameters.From || 'Unknown';

        const dialog = new frappe.ui.Dialog({
            title: __('Incoming Call'),
            fields: [
                {
                    fieldname: 'status',
                    fieldtype: 'HTML',
                    options: `<div style="text-align: center; font-size: 18px; margin: 20px 0;">
                                <strong>Ringing...</strong><br>Call from ${caller_id}
                              </div>`
                }
            ],
            primary_action_label: __('Accept'),
            primary_action: () => {
                call.accept();
                dialog.set_primary_action(__('End Call'), () => {
                    call.disconnect();
                    dialog.hide();
                });
                dialog.get_primary_btn().removeClass('btn-primary').addClass('btn-danger');
                dialog.get_secondary_btn().hide();
                dialog.fields_dict.status.$wrapper.html(`<div style="text-align: center; font-size: 18px; margin: 20px 0; color: green;">
                                <strong>In Call</strong><br>Connected with ${caller_id}
                              </div>`);
            }
        });

        dialog.set_secondary_action_label(__('Reject'));
        dialog.set_secondary_action(() => {
            call.reject();
            dialog.hide();
        });

        // Styling buttons to match Red / Green
        dialog.get_primary_btn().removeClass('btn-primary').addClass('btn-success');
        dialog.get_secondary_btn().removeClass('btn-default').addClass('btn-danger').css('color', 'white');

        dialog.show();

        call.on('disconnect', () => {
            dialog.hide();
            frappe.show_alert({message: 'Call Ended', indicator: 'orange'});
        });

        call.on('cancel', () => {
            dialog.hide();
            frappe.show_alert({message: 'Call Missed', indicator: 'red'});
        });
    },

    show_dialer: function(default_number = '') {
        if (!this.is_ready) {
            frappe.msgprint(__('Telephony service is not ready. Please check your connection and settings.'));
            return;
        }

        const dialog = new frappe.ui.Dialog({
            title: __('Softphone Dialer'),
            fields: [
                {
                    fieldname: 'phone_number',
                    fieldtype: 'Data',
                    label: __('Phone Number'),
                    default: default_number,
                    reqd: 1
                }
            ],
            primary_action_label: __('Call'),
            primary_action: (values) => {
                const params = { To: values.phone_number };
                const call = this.device.connect({ params: params });

                dialog.set_primary_action(__('End Call'), () => {
                    call.disconnect();
                    dialog.hide();
                });

                dialog.get_primary_btn().removeClass('btn-primary').addClass('btn-danger');

                call.on('disconnect', () => {
                    dialog.hide();
                    frappe.show_alert({message: 'Call Ended', indicator: 'orange'});
                });
            }
        });

        dialog.show();
    }
};

$(document).ready(function() {
    erpnext_enhancements.telephony.init();
});
