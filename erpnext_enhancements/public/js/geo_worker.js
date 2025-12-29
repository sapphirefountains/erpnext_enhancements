// geo_worker.js
// Dedicated Web Worker for Time Kiosk Geolocation Telemetry

const DB_NAME = 'TimeKioskDB';
const STORE_NAME = 'GeoLogs';
const INTERVAL_MS = 5 * 60 * 1000; // 5 minutes

let intervalId = null;
let currentEmployee = null;
let currentUser = null;
let csrfToken = null;

// --- INDEXED DB HELPERS ---
const openDB = () => {
    return new Promise((resolve, reject) => {
        const request = indexedDB.open(DB_NAME, 1);
        request.onupgradeneeded = (e) => {
            const db = e.target.result;
            if (!db.objectStoreNames.contains(STORE_NAME)) {
                db.createObjectStore(STORE_NAME, { autoIncrement: true });
            }
        };
        request.onsuccess = (e) => resolve(e.target.result);
        request.onerror = (e) => reject(e.target.error);
    });
};

const addToOfflineQueue = async (data) => {
    try {
        const db = await openDB();
        const tx = db.transaction(STORE_NAME, 'readwrite');
        tx.objectStore(STORE_NAME).add(data);
        return tx.complete;
    } catch (e) {
        console.error("Worker: Failed to queue offline data", e);
    }
};

const deleteFromOfflineQueue = async (key) => {
    try {
        const db = await openDB();
        const tx = db.transaction(STORE_NAME, 'readwrite');
        tx.objectStore(STORE_NAME).delete(key);
        return tx.complete;
    } catch (e) {
        console.error("Worker: Failed to delete offline data", e);
    }
};

const processOfflineQueue = async () => {
    if (!navigator.onLine) return;

    try {
        const db = await openDB();
        const tx = db.transaction(STORE_NAME, 'readonly');
        const store = tx.objectStore(STORE_NAME);
        // Use cursor to get key AND value
        const request = store.openCursor();

        request.onsuccess = async (event) => {
            const cursor = event.target.result;
            if (cursor) {
                const record = cursor.value;
                const key = cursor.key;

                try {
                    console.log(`Worker: Processing offline record ${key}`);
                    await sendLog(record, true); // isRetry = true

                    // If successful, delete THIS record
                    await deleteFromOfflineQueue(key);
                } catch (e) {
                    console.warn(`Worker: Failed to retry record ${key}, keeping in queue.`);
                }

                cursor.continue();
            }
        };
    } catch (e) {
        console.error("Worker: Failed to process offline queue", e);
    }
};

// --- API COMMUNICATION ---
const sendLog = async (payload, isRetry = false) => {
    try {
        // Construct standard FormData or JSON for Frappe API
        // Frappe usually accepts JSON if content-type is set, or form-data
        // We use JSON here.

        // Ensure timestamp is properly formatted string for Frappe
        // If it's a number (Date.now()), convert to "YYYY-MM-DD HH:mm:ss"
        let ts = payload.timestamp;
        if (typeof ts === 'number') {
            const d = new Date(ts);
            // Simple ISO-like format or let Frappe handle standard format
            // Frappe expects "YYYY-MM-DD HH:mm:ss" usually
            const pad = (n) => n.toString().padStart(2, '0');
            ts = `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
        }

        const formData = new FormData();
        formData.append('employee', payload.employee);
        formData.append('latitude', payload.latitude);
        formData.append('longitude', payload.longitude);
        formData.append('device_agent', payload.device_agent);
        formData.append('log_status', payload.log_status);
        formData.append('timestamp', ts);

        // Add CSRF token if available (Workers don't easily access document.cookie)
        // However, since it's same-origin, Fetch *should* handle cookies automatically.
        // X-Frappe-CSRF-Token might be needed for POST.
        // We will try without first, relying on session cookies. If strictly required, we might need it passed from main thread.
        // Assuming session cookie auth.

        const headers = {};
        if (csrfToken) {
            headers['X-Frappe-CSRF-Token'] = csrfToken;
        }

        const response = await fetch('/api/method/erpnext_enhancements.api.time_kiosk.log_geolocation', {
            method: 'POST',
            body: formData,
            headers: headers
        });

        if (!response.ok) {
            throw new Error(`Server returned ${response.status}`);
        }

        const result = await response.json();
        if (result.message && result.message.status === 'error') {
            throw new Error(result.message.message);
        }

        // Success
        postMessage({ type: 'log_success', timestamp: payload.timestamp, isRetry });

    } catch (e) {
        console.warn("Worker: Send failed", e);
        if (!isRetry) {
             // Only queue if it wasn't already a retry (to avoid infinite loops/duplication logic for now)
             // actually, we should always queue if failed and not in queue.
             // But if we are processing queue, we don't want to re-add immediately?
             // Simple logic: if live send fails, add to queue.
             addToOfflineQueue(payload);
             postMessage({ type: 'log_offline', timestamp: payload.timestamp });
        }
    }
};

// --- GEOLOCATION LOGIC ---
const fetchLocation = async () => {
    if (!currentEmployee) return;

    postMessage({ type: 'fetching_location' });

    // Permissions check (only available in some browsers in Worker, but let's try)
    let permissionGranted = true;
    try {
        if (navigator.permissions) {
            const result = await navigator.permissions.query({ name: 'geolocation' });
            if (result.state === 'denied') permissionGranted = false;
        }
    } catch (e) {
        // Ignore permission query error
    }

    if (!permissionGranted) {
        const payload = {
            employee: currentEmployee,
            latitude: 0,
            longitude: 0,
            device_agent: navigator.userAgent,
            log_status: 'Permission Denied',
            timestamp: Date.now()
        };
        sendLog(payload);
        postMessage({ type: 'permission_denied' });
        return;
    }

    // Acquire Position
    if (!navigator.geolocation) {
        const payload = {
            employee: currentEmployee,
            latitude: 0,
            longitude: 0,
            device_agent: navigator.userAgent,
            log_status: 'Error',
            timestamp: Date.now()
        };
        sendLog(payload);
        return;
    }

    navigator.geolocation.getCurrentPosition(
        (pos) => {
            const payload = {
                employee: currentEmployee,
                latitude: pos.coords.latitude,
                longitude: pos.coords.longitude,
                device_agent: navigator.userAgent,
                log_status: 'Success',
                timestamp: Date.now()
            };
            sendLog(payload);
        },
        (err) => {
            console.warn("Worker: GPS Error", err);
            // If error is permission denied (code 1)
            let status = 'Error';
            if (err.code === 1) status = 'Permission Denied';

            const payload = {
                employee: currentEmployee,
                latitude: 0,
                longitude: 0,
                device_agent: navigator.userAgent,
                log_status: status,
                timestamp: Date.now()
            };
            sendLog(payload);
            if (status === 'Permission Denied') postMessage({ type: 'permission_denied' });
        },
        {
            enableHighAccuracy: false, // Battery saving
            timeout: 10000,
            maximumAge: 0
        }
    );
};

// --- MESSAGE HANDLER ---
onmessage = function(e) {
    const { type, data } = e.data;

    if (type === 'start') {
        currentEmployee = data.employee;
        currentUser = data.user;
        if (data.csrf_token) {
            csrfToken = data.csrf_token;
        }
        console.log("Worker: Started for employee " + currentEmployee);

        // Immediate fetch
        fetchLocation();

        // Start Interval
        if (intervalId) clearInterval(intervalId);
        intervalId = setInterval(fetchLocation, INTERVAL_MS);

        // Attempt to flush offline queue
        processOfflineQueue();

    } else if (type === 'stop') {
        console.log("Worker: Stopped");
        if (intervalId) clearInterval(intervalId);
        intervalId = null;
        currentEmployee = null;
    } else if (type === 'csrf_token') {
        // If we need to store token for headers
        // self.csrf_token = data.token;
    }
};

// --- WAKE UP CHECK ---
let lastTick = Date.now();
setInterval(() => {
    const now = Date.now();
    const delta = now - lastTick;
    if (delta > INTERVAL_MS + 60000) { // If missed by more than 1 minute
        console.log("Worker: Wake up detected (delta " + delta + "ms). Triggering fetch.");
        if (currentEmployee) fetchLocation();
    }
    lastTick = now;
}, 10000); // Check every 10 seconds (cheap)
