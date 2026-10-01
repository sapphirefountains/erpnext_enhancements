/**
 * Telephony client — Twilio softphone + SMS dialer + incoming-call alert.
 *
 * Targets: the whole desk (provides a shared service used by form scripts) —
 *   global.
 * Loaded via: hooks.py `app_include_js` (global desk script).
 *
 * Defines erpnext_enhancements.telephony, which:
 *  - subscribes to the `triton_incoming_call` realtime event (published by
 *    api/telephony.notify_incoming_call when the Triton voice gateway reports
 *    a call state change) and renders a non-blocking floating call panel with
 *    the CRM-enriched caller, IVR stage/intent, and answered/missed outcomes;
 *  - lazy-loads the Twilio Voice SDK from a CDN and registers a WebRTC Device
 *    using a server-issued token (get_softphone_token; returns null for users
 *    outside Triton Settings.softphone_users — they keep the panel but skip
 *    the answer device). The Device's incoming call binds Accept/Decline/End
 *    into the same panel; the real caller is read from the TwiML <Parameter>s
 *    since <Dial callerId> rewrites the leg's From.
 *  - exposes show_sms_dialer() (sends via send_sms — backend only, no WebRTC
 *    needed) and show_dialer() for outbound calls. The Contact/Customer/Lead/
 *    Communication form scripts call into show_sms_dialer /
 *    trigger_outbound_call.
 *
 * How loud the alert is depends on whether this person can answer here.
 * The realtime event goes to every open desk, but only softphone users can
 * pick the call up, so only they are rung at:
 *
 *   everyone   — the panel (top-centre and pulsing while ringing, docked
 *                bottom-right once it is dealt with) and a flashing tab title,
 *                so a background tab still says something is happening;
 *   answerers  — plus a synthesized ringtone that plays in a background tab,
 *                a glow around the screen edge, a desktop notification that
 *                stays up until the call is answered or gone, and a vibrate on
 *                phones. Mutable per browser from the panel.
 *
 * The ringtone is WebAudio rather than the Twilio SDK's own incoming sound
 * (which is switched off below), for two reasons: the SDK only rings once OUR
 * leg arrives, which is after the caller has sat through the phone menu, and
 * one sound source is the only way to stop two from playing at once. Browsers
 * refuse audio until the page has had a click or keypress, so the context is
 * unlocked on the first one; a desk that has never been touched stays silent
 * and says so in the panel rather than failing quietly.
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
    _notification: null,
    // The call this desk has pressed Accept on. Accepting waits on the
    // microphone prompt, so the notice still reads "ringing" for a moment —
    // nothing may start ringing again in that window.
    _accepted_sid: null,

    // Attention state: the ringtone, the title flash and the edge glow.
    _audio_ctx: null,
    _ring_timer: null,
    _ring_stop_timer: null,
    _ringing_sid: null,
    _title_timer: null,
    _title_text: null,
    _title_base: null,
    _glow: null,
    _tab_id: Math.random().toString(36).slice(2),

    // Ringing stops by itself after this long even if no "ended" event ever
    // arrives — Twilio's longest ring is 60s, and a lost realtime message must
    // not leave a desk trilling all afternoon.
    MAX_RING_MS: 75000,
    MUTE_KEY: 'ee_call_ringer_muted',
    CLAIM_KEY: 'ee_call_ringer_claim',

    init: function() {
        this.setup_realtime();
        this._install_audio_unlock();
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
            this.start_alerting();
        } else if (data.event === 'caller_resolved' && same_call) {
            if (data.caller_name) {
                this.notice.caller_name = data.caller_name;
                this.render_panel();
                this._refresh_title_text();
            }
        } else if (data.event === 'answered' && same_call) {
            // If WE answered, the in-call panel is already showing — leave it.
            if (this.incoming_call && this.notice.state === 'in-call') return;
            this.stop_alerting();
            this.notice.state = 'answered';
            this.notice.agent_name = data.agent_name;
            this.render_panel();
            this.dismiss_soon(6000);
        } else if (data.event === 'ended' && same_call) {
            if (this.notice.state === 'in-call') return; // our disconnect handler owns it
            this.stop_alerting();
            const missed = ['no-answer', 'busy', 'failed', 'canceled'].includes(
                String(data.reason || '').toLowerCase()
            );
            // An answered call ending is just over; a never-answered one was missed.
            this.notice.state = this.notice.state === 'answered' ? 'over' : (missed ? 'missed' : 'over');
            this.render_panel();
            // A missed call stays until somebody closes it: the person who most
            // needs to see it is the one who was away from the desk.
            // (Clear first: a cancelled leg may already have scheduled one.)
            if (this.notice.state === 'missed') this._clear_dismiss_timer();
            else this.dismiss_soon(3000);
        }
    },

    // ------------------------------------------------------------------
    // Getting attention
    // ------------------------------------------------------------------

    /** True when this browser has a registered softphone, i.e. can pick up. */
    is_answerer: function() {
        return !!this.device;
    },

    is_muted: function() {
        try { return localStorage.getItem(this.MUTE_KEY) === '1'; } catch (e) { return false; }
    },

    set_muted: function(muted) {
        try {
            if (muted) localStorage.setItem(this.MUTE_KEY, '1');
            else localStorage.removeItem(this.MUTE_KEY);
        } catch (e) { /* storage blocked — the toggle just won't persist */ }
        if (muted) this._stop_ringtone();
        else if (this.notice && this.notice.state === 'ringing') this.start_alerting();
        this.render_panel();
    },

    /**
     * Idempotent: called on every ringing event and when our leg arrives, and
     * each piece only starts once per call.
     */
    start_alerting: function() {
        const notice = this.notice;
        if (!notice || notice.state !== 'ringing') return;
        if (this._accepted_sid && this._accepted_sid === notice.call_sid) return;

        this._start_title_flash();
        if (!this.is_answerer()) return;

        // Only ring out loud once phones are actually ringing — during the
        // phone menu there is nothing to pick up yet. Our own leg arriving
        // proves the same thing even if the realtime event was lost.
        const phones_ringing = notice.stage === 'agents' || !!this.incoming_call;
        this._show_glow();
        this.desktop_notify(notice);
        if (phones_ringing && !this.is_muted()) {
            this._start_ringtone(notice.call_sid);
            if (navigator.vibrate) {
                try { navigator.vibrate([400, 200, 400]); } catch (e) { /* noop */ }
            }
        }
    },

    stop_alerting: function() {
        this._stop_ringtone();
        this._stop_title_flash();
        this._hide_glow();
        if (this._notification) {
            try { this._notification.close(); } catch (e) { /* already gone */ }
            this._notification = null;
        }
    },

    desktop_notify: function(notice) {
        if (!('Notification' in window) || Notification.permission !== 'granted') return;
        if (this._notified_sid === notice.call_sid) return; // once per call
        // Somebody looking at this tab already has the panel in front of them.
        if (!document.hidden && document.hasFocus()) return;
        this._notified_sid = notice.call_sid;
        try {
            const title = __('Incoming call — {0}', [notice.caller_name || notice.from_number || __('Unknown')]);
            const body = [notice.intent, notice.from_number].filter(Boolean).join(' · ');
            const n = new Notification(title, {
                body: body,
                tag: 'triton-call-' + notice.call_sid,
                // Stays on screen until the call is answered or gone (stop_alerting
                // closes it) rather than sliding away after five seconds.
                requireInteraction: true,
            });
            n.onclick = () => { window.focus(); n.close(); };
            this._notification = n;
        } catch (e) { /* notification constructor can throw on some platforms */ }
    },

    /**
     * Must run from a click: browsers ignore a permission request that is not
     * the direct result of a user gesture, which is why the old in-event
     * request never produced a prompt.
     */
    request_desktop_alerts: function() {
        if (!('Notification' in window)) return;
        try {
            const p = Notification.requestPermission(() => this.render_panel());
            if (p && p.then) p.then(() => this.render_panel());
        } catch (e) { /* noop */ }
    },

    _install_audio_unlock: function() {
        const unlock = () => {
            const ctx = this._ensure_audio();
            if (!ctx) return;
            const unlocked = () => {
                if (ctx.state !== 'running') return;
                window.removeEventListener('pointerdown', unlock, true);
                window.removeEventListener('keydown', unlock, true);
                // A call already ringing when the page finally got its first
                // click starts ringing out loud now rather than next call.
                if (this.notice && this.notice.state === 'ringing') {
                    this.start_alerting();
                    this.render_panel();
                }
            };
            if (ctx.state === 'running') unlocked();
            else ctx.resume().then(unlocked).catch(() => { /* still locked */ });
        };
        window.addEventListener('pointerdown', unlock, true);
        window.addEventListener('keydown', unlock, true);
    },

    /** The AudioContext, created/resumed if possible; null when unsupported. */
    _ensure_audio: function() {
        const Ctx = window.AudioContext || window.webkitAudioContext;
        if (!Ctx) return null;
        if (!this._audio_ctx) {
            try { this._audio_ctx = new Ctx(); } catch (e) { return null; }
        }
        if (this._audio_ctx.state === 'suspended') {
            this._audio_ctx.resume().catch(() => { /* still locked */ });
        }
        return this._audio_ctx;
    },

    _audio_locked: function() {
        return !this._audio_ctx || this._audio_ctx.state !== 'running';
    },

    /**
     * Every open desk tab gets the same realtime event; only one per browser
     * should make a noise. The first tab whose audio is actually running takes
     * the call, and the rest stay quiet (they still flash).
     */
    _claim_ringer: function(sid) {
        try {
            const now = Date.now();
            const cur = JSON.parse(localStorage.getItem(this.CLAIM_KEY) || 'null');
            if (cur && cur.sid === sid && cur.tab !== this._tab_id && now - cur.ts < this.MAX_RING_MS) {
                return false;
            }
            localStorage.setItem(this.CLAIM_KEY, JSON.stringify({ sid: sid, tab: this._tab_id, ts: now }));
        } catch (e) { /* storage blocked — ring in every tab rather than none */ }
        return true;
    },

    _start_ringtone: function(sid) {
        if (this._ring_timer) return;
        this._ensure_audio();
        if (this._audio_locked()) {
            this.render_panel(); // shows the "click anywhere to hear calls" hint
            return;
        }
        if (!this._claim_ringer(sid)) return;
        this._ringing_sid = sid;
        this._ring_burst();
        this._ring_timer = setInterval(() => this._ring_burst(), 3000);
        this._ring_stop_timer = setTimeout(() => this._stop_ringtone(), this.MAX_RING_MS);
    },

    _stop_ringtone: function() {
        if (this._ring_timer) clearInterval(this._ring_timer);
        if (this._ring_stop_timer) clearTimeout(this._ring_stop_timer);
        this._ring_timer = null;
        this._ring_stop_timer = null;
        try {
            const cur = JSON.parse(localStorage.getItem(this.CLAIM_KEY) || 'null');
            if (cur && cur.tab === this._tab_id) localStorage.removeItem(this.CLAIM_KEY);
        } catch (e) { /* noop */ }
        this._ringing_sid = null;
    },

    /**
     * One "brr-brr": two 0.4s trills of a two-note chord, each trilled at
     * 18Hz the way a bell ringer is, so it reads as a phone rather than as a
     * notification chime. Synthesized, so there is no asset to cache-bust.
     */
    _ring_burst: function() {
        const ctx = this._audio_ctx;
        if (!ctx || ctx.state !== 'running') return;
        const t0 = ctx.currentTime + 0.02;
        const out = ctx.createGain();
        out.gain.value = 0;
        out.connect(ctx.destination);

        [[0, 0.4], [0.6, 1.0]].forEach(([start, end]) => {
            out.gain.setValueAtTime(0, t0 + start);
            out.gain.linearRampToValueAtTime(0.16, t0 + start + 0.02);
            out.gain.setValueAtTime(0.16, t0 + end - 0.03);
            out.gain.linearRampToValueAtTime(0, t0 + end);
        });

        const trill = ctx.createGain();
        trill.gain.value = 0.5;
        trill.connect(out);
        const lfo = ctx.createOscillator();
        const depth = ctx.createGain();
        lfo.frequency.value = 18;
        depth.gain.value = 0.5;
        lfo.connect(depth);
        depth.connect(trill.gain);

        const oscs = [784, 988].map((freq) => {
            const o = ctx.createOscillator();
            o.type = 'triangle';
            o.frequency.value = freq;
            o.connect(trill);
            return o;
        });
        [lfo].concat(oscs).forEach((o) => {
            o.start(t0);
            o.stop(t0 + 1.05);
        });
        oscs[0].onended = () => {
            try { out.disconnect(); } catch (e) { /* noop */ }
        };
    },

    _title_for: function(notice) {
        return '📞 ' + __('Call: {0}', [notice.caller_name || notice.from_number || __('Unknown')]);
    },

    _start_title_flash: function() {
        if (!this.notice) return;
        this._title_text = this._title_for(this.notice);
        if (this._title_timer) return;
        let on = false;
        this._title_timer = setInterval(() => {
            // Frappe retitles the tab on every route change; pick up whatever
            // it set so stopping restores the page you are now on, not the old one.
            if (document.title !== this._title_text) this._title_base = document.title;
            on = !on;
            document.title = on ? this._title_text : (this._title_base || '');
        }, 900);
    },

    _refresh_title_text: function() {
        if (this._title_timer && this.notice) this._title_text = this._title_for(this.notice);
    },

    _stop_title_flash: function() {
        if (this._title_timer) clearInterval(this._title_timer);
        this._title_timer = null;
        if (this._title_base !== null && document.title === this._title_text) {
            document.title = this._title_base;
        }
        this._title_text = null;
        this._title_base = null;
    },

    _show_glow: function() {
        if (this._glow && document.body.contains(this._glow[0])) return;
        this._inject_panel_styles();
        this._glow = $('<div class="telephony-ring-glow" aria-hidden="true"></div>').appendTo('body');
    },

    _hide_glow: function() {
        if (this._glow) this._glow.remove();
        this._glow = null;
    },

    // ------------------------------------------------------------------
    // Floating call panel (replaces the old blocking dialog)
    // ------------------------------------------------------------------
    _inject_panel_styles: function() {
        if (document.getElementById('telephony-call-panel-styles')) return;
        $("<style id='telephony-call-panel-styles'>").html(`
            .telephony-call-panel {
                position: fixed; right: 20px; bottom: 20px; z-index: 1060;
                width: min(340px, calc(100vw - 32px)); padding: 14px 16px;
                background: var(--card-bg); color: var(--text-color);
                border: 1px solid var(--border-color); border-radius: 10px;
                box-shadow: var(--shadow-lg, 0 8px 24px rgba(0,0,0,0.25));
                font-size: var(--text-md, 13px);
            }
            /* Ringing: front and centre under the navbar, bigger, pulsing. */
            .telephony-call-panel.tcp-ringing {
                right: auto; bottom: auto; top: 64px; left: 50%;
                transform: translateX(-50%);
                width: min(400px, calc(100vw - 32px)); padding: 18px 20px;
                border: 2px solid var(--green-500, #28a745);
                animation: tcp-halo 1.6s ease-out infinite;
            }
            @keyframes tcp-halo {
                0%   { box-shadow: 0 0 0 0 rgba(40,167,69,0.55), var(--shadow-lg, 0 8px 24px rgba(0,0,0,0.25)); }
                70%  { box-shadow: 0 0 0 18px rgba(40,167,69,0), var(--shadow-lg, 0 8px 24px rgba(0,0,0,0.25)); }
                100% { box-shadow: 0 0 0 0 rgba(40,167,69,0), var(--shadow-lg, 0 8px 24px rgba(0,0,0,0.25)); }
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
            .telephony-call-panel.tcp-ringing .tcp-dot { background: var(--green-500, #28a745); animation: tcp-pulse 1.2s infinite; }
            .telephony-call-panel.tcp-in-call .tcp-dot { background: var(--green-500, #28a745); }
            .telephony-call-panel.tcp-missed .tcp-dot { background: var(--red-500, #e24c4c); }
            .telephony-call-panel.tcp-missed { border-color: var(--red-500, #e24c4c); }
            @keyframes tcp-pulse { 0% { opacity: 1; } 50% { opacity: 0.3; } 100% { opacity: 1; } }
            .telephony-call-panel .tcp-head { display: flex; align-items: center; gap: 12px; }
            .telephony-call-panel .tcp-icon {
                flex: none; width: 44px; height: 44px; border-radius: 50%;
                display: flex; align-items: center; justify-content: center;
                background: var(--green-500, #28a745); color: #fff; font-size: 22px;
            }
            .telephony-call-panel.tcp-ringing .tcp-icon { animation: tcp-shake 1s ease-in-out infinite; }
            @keyframes tcp-shake {
                0%, 50%, 100% { transform: rotate(0); }
                10%, 30% { transform: rotate(-14deg); }
                20%, 40% { transform: rotate(14deg); }
            }
            .telephony-call-panel .tcp-caller { font-weight: 600; font-size: var(--text-lg, 15px); }
            .telephony-call-panel.tcp-ringing .tcp-caller { font-size: var(--text-2xl, 20px); line-height: 1.2; }
            .telephony-call-panel .tcp-sub { color: var(--text-muted); margin-top: 2px; word-break: break-word; }
            .telephony-call-panel .tcp-context { color: var(--text-muted); margin-top: 6px; font-size: var(--text-sm, 12px); }
            .telephony-call-panel .tcp-hint { color: var(--text-muted); margin-top: 8px; font-size: var(--text-sm, 12px); }
            .telephony-call-panel .tcp-actions { display: flex; gap: 8px; margin-top: 12px; }
            .telephony-call-panel .tcp-actions:empty { display: none; }
            .telephony-call-panel .tcp-actions .btn { flex: 1; }
            .telephony-call-panel.tcp-ringing .tcp-actions .btn { padding: 10px 12px; font-size: var(--text-md, 14px); font-weight: 600; }
            .telephony-call-panel .tcp-foot {
                display: flex; flex-wrap: wrap; gap: 12px; margin-top: 10px;
                font-size: var(--text-sm, 12px);
            }
            .telephony-call-panel .tcp-foot:empty { display: none; }
            .telephony-call-panel .tcp-link {
                background: none; border: none; padding: 0; cursor: pointer;
                color: var(--text-muted); text-decoration: underline;
            }
            .telephony-call-panel .tcp-close {
                position: absolute; top: 8px; right: 10px; cursor: pointer;
                color: var(--text-muted); background: none; border: none; font-size: 14px;
            }
            /* Screen-edge glow for answerers while a call rings. Never takes clicks. */
            .telephony-ring-glow {
                position: fixed; inset: 0; z-index: 1059; pointer-events: none;
                box-shadow: inset 0 0 0 4px var(--green-500, #28a745), inset 0 0 40px rgba(40,167,69,0.45);
                animation: tcp-glow 1.2s ease-in-out infinite;
            }
            @keyframes tcp-glow { 0%, 100% { opacity: 1; } 50% { opacity: 0.25; } }
            @media (prefers-reduced-motion: reduce) {
                .telephony-call-panel.tcp-ringing,
                .telephony-call-panel .tcp-icon,
                .telephony-call-panel .tcp-dot,
                .telephony-ring-glow { animation: none !important; }
            }
        `).appendTo('head');
    },

    get_panel: function() {
        this._inject_panel_styles();
        if (!this._panel || !document.body.contains(this._panel[0])) {
            this._panel = $('<div class="telephony-call-panel" role="alertdialog" aria-live="assertive"></div>').appendTo('body');
        }
        return this._panel;
    },

    dismiss_panel: function() {
        this._clear_dismiss_timer();
        this.stop_alerting();
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

        // What an answerer needs to know when they can't pick up yet, or
        // won't hear the next one.
        let hint = '';
        if (state === 'ringing' && this.is_answerer()) {
            if (!this.incoming_call && notice.stage === 'menu') {
                hint = __('Choosing a menu option — Accept appears when your phone rings.');
            } else if (!this.is_muted() && this._audio_locked()) {
                hint = __('Click anywhere on this page once to hear calls ring.');
            }
        }

        panel.attr('class', 'telephony-call-panel ' + dot_cls);
        panel.html(`
            <button class="tcp-close" title="${__('Dismiss')}">&times;</button>
            <div class="tcp-kicker"><span class="tcp-dot"></span><span>${kicker}</span></div>
            <div class="tcp-head">
                ${state === 'ringing' ? '<div class="tcp-icon" aria-hidden="true">📞</div>' : ''}
                <div>
                    <div class="tcp-caller">${caller}</div>
                    ${sub_bits.length ? `<div class="tcp-sub">${sub_bits.join(' · ')}</div>` : ''}
                </div>
            </div>
            ${context_html}
            ${hint ? `<div class="tcp-hint">${esc(hint)}</div>` : ''}
            <div class="tcp-actions"></div>
            <div class="tcp-foot"></div>
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

        const foot = panel.find('.tcp-foot');
        if (state === 'ringing' && this.is_answerer()) {
            const muted = this.is_muted();
            $(`<button class="tcp-link">${muted ? __('Unmute ringer') : __('Mute ringer')}</button>`)
                .appendTo(foot)
                .on('click', () => this.set_muted(!muted));
            if ('Notification' in window && Notification.permission === 'default') {
                $(`<button class="tcp-link">${__('Alert me when this tab is in the background')}</button>`)
                    .appendTo(foot)
                    .on('click', () => this.request_desktop_alerts());
            }
        }
    },

    accept_incoming: function() {
        const call = this.incoming_call;
        if (!call) return;
        if (this.notice) this._accepted_sid = this.notice.call_sid;
        this.stop_alerting();
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

        // Our own ringtone replaces the SDK's — see the header comment.
        try {
            if (this.device.audio) this.device.audio.incoming(false);
        } catch (e) { /* older SDK: worst case both play */ }

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
        this.start_alerting();

        call.on('disconnect', () => {
            this.incoming_call = null;
            this.stop_alerting();
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
            this.stop_alerting();
            if (this.notice && this.notice.state === 'ringing') {
                this.render_panel();
                this.dismiss_soon(8000);
            }
        });

        call.on('reject', () => {
            this.incoming_call = null;
            this.stop_alerting();
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
