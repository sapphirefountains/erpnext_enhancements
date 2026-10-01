// Minimal Chrome DevTools Protocol driver: launch headless Chrome, open a page, evaluate, click, screenshot.
const { spawn } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');

const CHROME = process.env.CHROME || 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
const sleep = ms => new Promise(r => setTimeout(r, ms));

async function launch(port) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'tcv-'));
  const proc = spawn(CHROME, ['--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
    '--remote-debugging-port=' + port, '--user-data-dir=' + dir, '--hide-scrollbars', 'about:blank'], { stdio: 'ignore' });
  for (let i = 0; i < 80; i++) {
    try { const r = await fetch('http://127.0.0.1:' + port + '/json/version'); if (r.ok) break; } catch (e) {}
    await sleep(150);
  }
  return { proc, dir, port };
}

async function open(b, url, w, h) {
  const r = await fetch('http://127.0.0.1:' + b.port + '/json/new?' + encodeURIComponent('about:blank'), { method: 'PUT' });
  const t = await r.json();
  const ws = new WebSocket(t.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  let id = 0; const waits = {}; const events = [];
  ws.onmessage = m => { const d = JSON.parse(m.data); if (d.id && waits[d.id]) { waits[d.id](d); delete waits[d.id]; } else if (d.method) events.push(d); };
  const send = (method, params = {}) => new Promise(res => { const i = ++id; waits[i] = res; ws.send(JSON.stringify({ id: i, method, params })); });
  await send('Page.enable'); await send('Runtime.enable');
  await send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: 1, mobile: false });
  const page = {
    send, events,
    async goto(u) {
      events.length = 0; await send('Page.navigate', { url: u });
      for (let i = 0; i < 200; i++) { if (events.some(e => e.method === 'Page.loadEventFired')) break; await sleep(100); }
      await sleep(900);
    },
    async eval(expr) {
      const r = await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true });
      if (r.result && r.result.exceptionDetails) throw new Error('eval failed: ' + JSON.stringify(r.result.exceptionDetails).slice(0, 600));
      return r.result && r.result.result ? r.result.result.value : undefined;
    },
    async click(x, y) {
      for (const type of ['mouseMoved', 'mousePressed', 'mouseReleased'])
        await send('Input.dispatchMouseEvent', { type, x, y, button: 'left', clickCount: 1 });
      await sleep(350);
    },
    async key(k) {
      await send('Input.dispatchKeyEvent', { type: 'keyDown', key: k, text: k.length === 1 ? k : undefined });
      await send('Input.dispatchKeyEvent', { type: 'keyUp', key: k });
      await sleep(250);
    },
    async type(text) { await send('Input.insertText', { text }); await sleep(200); },
    async resize(w2, h2) { await send('Emulation.setDeviceMetricsOverride', { width: w2, height: h2, deviceScaleFactor: 1, mobile: w2 < 600 }); await sleep(400); },
    async shot(file) {
      const r = await send('Page.captureScreenshot', { format: 'png' });
      fs.writeFileSync(file, Buffer.from(r.result.data, 'base64'));
    },
    errors() { return events.filter(e => e.method === 'Runtime.exceptionThrown').map(e => JSON.stringify(e.params.exceptionDetails).slice(0, 400)); },
    close() { try { ws.close(); } catch (e) {} },
  };
  await page.goto(url);
  return page;
}

function shutdown(b) { try { b.proc.kill(); } catch (e) {} }

module.exports = { launch, open, shutdown, sleep };
