/**
 * Telephony client — Twilio softphone + SMS dialer + incoming-call panel.
 *
 * Targets: the whole desk (provides a shared service used by form scripts) —
 *   global.
 * Loaded via: hooks.py `app_include_js` (global desk script).
 *
 * Defines erpnext_enhancements.telephony, which:
 *  - subscribes to the `triton_incoming_call` realtime event (published by
 *    api/telephony.notify_incoming_call when the Triton voice gateway reports
 *    a call state change) and renders a non-blocking floating call panel with
 *    the CRM-enriched caller, IVR stage/intent, and answered/missed outcomes,
 *    plus a desktop Notification;
 *  - lazy-loads the Twilio Voice SDK from a CDN and registers a WebRTC Device
 *    using a server-issued token (get_softphone_token; returns null for users
 *    outside Triton Settings.softphone_users — they keep the notifications
 *    but skip the answer device). The Device's incoming call binds
 *    Accept/Decline/End into the same panel; the real caller is read from the
 *    TwiML <Parameter>s since <Dial callerId> rewrites the leg's From.
 *  - exposes show_sms_dialer() (sends via send_sms — backend only, no WebRTC
 *    needed) and show_dialer() for outbound calls. The Contact/Customer/Lead/
 *    Communication form scripts call into show_sms_dialer /
 *    trigger_outbound_call.
 */
frappe.provide('erpnext_enhancements.telephony');

erpnext_enhancements.telephony = {
    device: null,
    identity: 'nikolas_erpnext',
    is_ready: false,

    // Incoming-call panel state: the latest realtime payload (by call_sid),
    // the live Twilio call object once OUR device leg rings, and the panel DOM.
    notice: null,
    incoming_call: null,
    _panel: null,
    _dismiss_timer: null,
    _notified_sid: null,

    init: function() {
        this.setup_realtime();
        this.load_twilio_script()
            .then(() => this.fetch_token())
            .then((token) => {
                if (token) {
                    this.setup_device(token);
                } else {
                    console.log('[telephony] Softphone answer device disabled for this user; incoming-call notifications remain active.');
                }
            })
            .catch(err => {
                console.error("Telephony Initialization Failed:", err);
            });
    },

    // ------------------------------------------------------------------
    // Realtime call-lifecycle events (Triton gateway → notify_incoming_call
    // → publish_realtime → every open desk).
    // ------------------------------------------------------------------
    setup_realtime: function() {
        frappe.realtime.on('triton_incoming_call', (data) => {
            try {
                this.handle_call_event(data || {});
            } catch (e) {
                console.error('[telephony] call event failed', e);
            }
        });
    },

    handle_call_event: function(data) {
        if (!data.call_sid || !data.event) return;
        const same_call = this.notice && this.notice.call_sid === data.call_sid;

        if (data.event === 'ringing') {
            // stage "menu" (caller in IVR) then stage "agents" (browsers ringing).
            // Keep enrichment from the earlier event if the later one lacks it.
            const base = same_call ? this.notice : {};
            this.notice = Object.assign({}, base, data, {
                caller_name: data.caller_name || base.caller_name || null,
                customer: data.customer || base.customer || null,
                contact: data.contact || base.contact || null,
                context: data.context || base.context || [],
                state: 'ringing',
            });
            this._clear_dismiss_timer();
            this.render_panel();
            this.desktop_notify(this.notice);
        } else if (data.event === 'caller_resolved' && same_call) {
            if (data.caller_name) {
                this.notice.caller_name = data.caller_name;
                this.render_panel();
            }
        } else if (data.event === 'answered' && same_call) {
            // If WE answered, the in-call panel is already showing — leave it.
            if (this.incoming_call && this.notice.state === 'in-call') return;
            this.notice.state = 'answered';
            this.notice.agent_name = data.agent_name;
            this.render_panel();
            this.dismiss_soon(6000);
        } else if (data.event === 'ended' && same_call) {
            if (this.notice.state === 'in-call') return; // our disconnect handler owns it
            const missed = ['no-answer', 'busy', 'failed', 'canceled'].includes(
                String(data.reason || '').toLowerCase()
            );
            // An answered call ending is just over; a never-answered one was missed.
            this.notice.state = this.notice.state === 'answered' ? 'over' : (missed ? 'missed' : 'over');
            this.render_panel();
            this.dismiss_soon(this.notice.state === 'missed' ? 8000 : 3000);
        }
    },

    desktop_notify: function(notice) {
        if (!('Notification' in window)) return;
        if (Notification.permission === 'default') {
            // May be ignored without a user gesture; harmless either way.
            try { Notification.requestPermission(); } catch (e) { /* noop */ }
            return;
        }
        if (Notification.permission !== 'granted') return;
        if (this._notified_sid === notice.call_sid) return; // once per call
        this._notified_sid = notice.call_sid;
        try {
            const title = __('Incoming call — {0}', [notice.caller_name || notice.from_number || __('Unknown')]);
            const body = [notice.intent, notice.from_number].filter(Boolean).join(' · ');
            const n = new Notification(title, { body: body, tag: 'triton-call-' + notice.call_sid });
            n.onclick = () => { window.focus(); n.close(); };
        } catch (e) { /* notification constructor can throw on some platforms */ }
    },

    // ------------------------------------------------------------------
    // Floating call panel (replaces the old blocking dialog)
    // ------------------------------------------------------------------
    _inject_panel_styles: function() {
        if (document.getElementById('telephony-call-panel-styles')) return;
        $("<style id='telephony-call-panel-styles'>").html(`
            .telephony-call-panel {
                position: fixed; right: 20px; bottom: 20px; z-index: 1060;
                width: 320px; padding: 14px 16px;
                background: var(--card-bg); color: var(--text-color);
                border: 1px solid var(--border-color); border-radius: 10px;
                box-shadow: var(--shadow-lg, 0 8px 24px rgba(0,0,0,0.25));
                font-size: var(--text-md, 13px);
            }
            .telephony-call-panel .tcp-kicker {
                display: flex; align-items: center; gap: 8px;
                text-transform: uppercase; letter-spacing: 0.08em;
                font-size: var(--text-sm, 12px); color: var(--text-muted);
                margin-bottom: 6px;
            }
            .telephony-call-panel .tcp-dot {
                width: 9px; height: 9px; border-radius: 50%;
                background: var(--blue-500, #2490ef);
            }
            .telephony-call-panel.tcp-ringing .tcp-dot { animation: tcp-pulse 1.2s infinite; }
            .telephony-call-panel.tcp-in-call .tcp-dot { background: var(--green-500, #28a745); }
            .telephony-call-panel.tcp-missed .tcp-dot { background: var(--red-500, #e24c4c); }
            @keyframes tcp-pulse { 0% { opacity: 1; } 50% { opacity: 0.3; } 100% { opacity: 1; } }
            .telephony-call-panel .tcp-caller { font-weight: 600; font-size: var(--text-lg, 15px); }
            .telephony-call-panel .tcp-sub { color: var(--text-muted); margin-top: 2px; word-break: break-word; }
            .telephony-call-panel .tcp-context { color: var(--text-muted); margin-top: 6px; font-size: var(--text-sm, 12px); }
            .telephony-call-panel .tcp-actions { display: flex; gap: 8px; margin-top: 12px; }
            .telephony-call-panel .tcp-actions .btn { flex: 1; }
            .telephony-call-panel .tcp-close {
                position: absolute; top: 8px; right: 10px; cursor: pointer;
                color: var(--text-muted); background: none; border: none; font-size: 14px;
            }
        `).appendTo('head');
    },

    get_panel: function() {
        this._inject_panel_styles();
        if (!this._panel || !document.body.contains(this._panel[0])) {
            this._panel = $('<div class="telephony-call-panel"></div>').appendTo('body');
        }
        return this._panel;
    },

    dismiss_panel: function() {
        this._clear_dismiss_timer();
        if (this._panel) {
            this._panel.remove();
            this._panel = null;
        }
        this.notice = null;
        this.incoming_call = null;
    },

    dismiss_soon: function(ms) {
        this._clear_dismiss_timer();
        this._dismiss_timer = setTimeout(() => this.dismiss_panel(), ms);
    },

    _clear_dismiss_timer: function() {
        if (this._dismiss_timer) {
            clearTimeout(this._dismiss_timer);
            this._dismiss_timer = null;
        }
    },

    render_panel: function() {
        const notice = this.notice;
        if (!notice) return;
        const esc = frappe.utils.escape_html;
        const panel = this.get_panel();
        const state = notice.state || 'ringing';
        const caller = esc(notice.caller_name || notice.from_number || __('Unknown Caller'));
        const number = notice.caller_name && notice.from_number ? esc(notice.from_number) : '';

        let kicker, dot_cls = '';
        if (state === 'ringing') {
            kicker = notice.stage === 'menu' ? __('Incoming Call — in phone menu') : __('Incoming Call — ringing');
            dot_cls = 'tcp-ringing';
        } else if (state === 'in-call') {
            kicker = __('In Call');
            dot_cls = 'tcp-in-call';
        } else if (state === 'answered') {
            kicker = notice.agent_name
                ? __('Answered by {0}', [esc(notice.agent_name)])
                : __('Call answered');
        } else if (state === 'missed') {
            kicker = __('Missed Call');
            dot_cls = 'tcp-missed';
        } else {
            kicker = __('Call Ended');
        }

        const customer_link = notice.customer
            ? `<a href="/app/customer/${encodeURIComponent(notice.customer)}">${esc(notice.customer)}</a>`
            : '';
        const context_html = (notice.context || []).length
            ? `<div class="tcp-context">${notice.context.map(esc).join('<br>')}</div>`
            : '';
        const sub_bits = [number, notice.intent ? esc(notice.intent) : '', customer_link].filter(Boolean);

        panel.attr('class', 'telephony-call-panel ' + dot_cls);
        panel.html(`
            <button class="tcp-close" title="${__('Dismiss')}">&times;</button>
            <div class="tcp-kicker"><span class="tcp-dot"></span><span>${kicker}</span></div>
            <div class="tcp-caller">${caller}</div>
            ${sub_bits.length ? `<div class="tcp-sub">${sub_bits.join(' · ')}</div>` : ''}
            ${context_html}
            <div class="tcp-actions"></div>
        `);
        panel.find('.tcp-close').on('click', () => {
            // Dismissing the panel never rejects the call — other answerers
            // (Triton, cell forward) keep ringing.
            this.dismiss_panel();
        });

        const actions = panel.find('.tcp-actions');
        if (state === 'ringing' && this.incoming_call) {
            const accept = $(`<button class="btn btn-success btn-sm">${__('Accept')}</button>`).appendTo(actions);
            const reject = $(`<button class="btn btn-danger btn-sm">${__('Decline')}</button>`).appendTo(actions);
            accept.on('click', () => this.accept_incoming());
            reject.on('click', () => {
                try { this.incoming_call.reject(); } catch (e) { /* already gone */ }
                this.dismiss_panel();
            });
        } else if (state === 'in-call' && this.incoming_call) {
            const end = $(`<button class="btn btn-danger btn-sm">${__('End Call')}</button>`).appendTo(actions);
            end.on('click', () => {
                try { this.incoming_call.disconnect(); } catch (e) { /* already gone */ }
            });
        }
    },

    accept_incoming: function() {
        const call = this.incoming_call;
        if (!call) return;
        this.request_permissions().then(() => {
            call.accept();
            if (this.notice) {
                this.notice.state = 'in-call';
                this.render_panel();
            }
        }).catch(err => {
            console.error('Microphone access denied', err);
        });
    },

    load_twilio_script: function() {
        return new Promise((resolve, reject) => {
            if (window.Twilio && window.Twilio.Device) {
                return resolve();
            }
            const script = document.createElement('script');
            script.src = 'https://cdn.jsdelivr.net/npm/@twilio/voice-sdk@2.18.1/dist/twilio.min.js';
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
                    // null = this user isn't in Triton Settings.softphone_users;
                    // skip the answer device but keep the notifications.
                    resolve(r.message || null);
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

        // The access token expires after 1 hour, after which Twilio silently
        // unregisters the device — desk tabs stay open all day here, so
        // without this refresh the softphone looked "Registered" in the
        // morning but never rang in the afternoon (panel without
        // Accept/Decline). The SDK fires this ~10s before expiry.
        this.device.on('tokenWillExpire', () => {
            this.fetch_token()
                .then((token) => {
                    if (token) {
                        this.device.updateToken(token);
                        console.log('[telephony] Softphone token refreshed');
                    } else {
                        // User was removed from softphone_users since page load.
                        console.log('[telephony] Softphone disabled for this user; releasing device.');
                        this.device.destroy();
                        this.device = null;
                        this.is_ready = false;
                    }
                })
                .catch((err) => {
                    console.error('[telephony] Softphone token refresh failed:', err);
                });
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
        // The TwiML <Dial> sets callerId to the business number, so the leg's
        // From is useless — the gateway passes the real caller (and the parent
        // call SID for matching against realtime events) as <Parameter>s.
        const params = call.customParameters || new Map();
        const parent_sid = params.get('parent_call_sid') || call.parameters.CallSid || null;
        const caller_number = params.get('caller_number') || call.parameters.From || null;

        this.incoming_call = call;
        if (this.notice && this.notice.call_sid === parent_sid) {
            this.notice.state = 'ringing';
            this.notice.stage = 'agents';
        } else {
            // Realtime event hasn't arrived (or Triton is older) — build the
            // notice from the call leg alone.
            this.notice = {
                call_sid: parent_sid,
                from_number: caller_number,
                caller_name: params.get('caller_name') || null,
                intent: params.get('intent') || null,
                stage: 'agents',
                state: 'ringing',
            };
        }
        this._clear_dismiss_timer();
        this.render_panel();

        call.on('disconnect', () => {
            this.incoming_call = null;
            if (this.notice) {
                this.notice.state = 'over';
                this.render_panel();
                this.dismiss_soon(3000);
            }
            frappe.show_alert({ message: __('Call Ended'), indicator: 'orange' });
        });

        // Twilio cancels our leg when the caller hangs up OR someone else
        // answers first; the realtime answered/ended event that follows
        // updates the panel to say which.
        call.on('cancel', () => {
            this.incoming_call = null;
            if (this.notice && this.notice.state === 'ringing') {
                this.render_panel();
                this.dismiss_soon(8000);
            }
        });

        call.on('reject', () => {
            this.incoming_call = null;
        });
    },


    show_sms_dialer: function(default_number = '', reference_doctype = '', reference_docname = '', prefilled_message = '') {
        // SMS relies entirely on the Frappe backend, so it shouldn't be blocked 
        // by the WebRTC Voice connection status.

        const dialog = new frappe.ui.Dialog({
            title: __('Send SMS'),
            fields: [
                {
                    fieldname: 'phone_number',
                    fieldtype: 'Data',
                    label: __('Phone Number'),
                    default: default_number,
                    reqd: 1
                },
                {
                    fieldname: 'message',
                    fieldtype: 'Small Text',
                    label: __('Message'),
                    reqd: 1,
                    default: prefilled_message
                },
                {
                    fieldname: 'attachments',
                    fieldtype: 'Attach',
                    label: __('Attach Media (Optional)')
                }
            ],
            primary_action_label: __('Send'),
            primary_action: (values) => {
                let media_urls = [];

                let process_send = () => {
                    dialog.get_primary_btn().prop('disabled', true).text(__('Sending...'));
                    frappe.call({
                        method: 'erpnext_enhancements.api.telephony.send_sms',
                        args: {
                            target_number: values.phone_number,
                            message: values.message,
                            media_urls: media_urls,
                            reference_doctype: reference_doctype,
                            reference_docname: reference_docname
                        },
                        callback: function(r) {
                            if (!r.exc) {
                                frappe.show_alert({message: __('SMS Sent'), indicator: 'green'});
                                dialog.hide();
                            } else {
                                dialog.get_primary_btn().prop('disabled', false).text(__('Send'));
                            }
                        }
                    });
                };

                if (values.attachments) {
                    let base_url = frappe.urllib.get_base_url();
                    media_urls.push(`${base_url}${values.attachments}`);
                }
                process_send();
            }
        });

        dialog.show();
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
                this.request_permissions().then(() => {
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
                }).catch(err => {
                    console.error("Microphone access denied", err);
                });
            }
        });

        dialog.show();
    }
};

$(document).ready(function() {
    erpnext_enhancements.telephony.init();
});
