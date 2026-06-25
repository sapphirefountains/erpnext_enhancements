/**
 * Wall / TV Display front-end (served at /wall; shell: www/wall.py + wall.html).
 *
 * Vanilla JS on purpose (kiosk precedent — no framework, no build step):
 * renders the briefing band (today's tasks / overdue / today's schedule),
 * the auto-rotating per-project carousel with an SVG task-completion donut,
 * and the Open-Meteo weather chip, all into #wall-root.
 *
 * Data: window.WALL_BOOT (server-injected first paint), then a plain GET to
 * get_wall_dashboard_data every settings.refresh_seconds. A 401/403 on
 * refresh means the session died — reload, which bounces through /login.
 *
 * Deploy pickup, two belts:
 *   1. Service worker /wall-sw.js?v=WALL_BUILD, reg.update() every 60s,
 *      immediate reload on controllerchange (nothing to protect on a display).
 *   2. Each data payload carries deploy_version; mismatch with WALL_BUILD
 *      reloads even if the worker never installed.
 *
 * Weather: client-side fetch straight to Open-Meteo (keyless, CORS-open) —
 * a Pi that can't reach the internet can't reach Frappe either, so a server
 * proxy would buy nothing.
 */
(function () {
  "use strict";

  var DATA_ENDPOINT =
    "/api/method/erpnext_enhancements.api.task_dashboard.get_wall_dashboard_data";
  var SW_CHECK_MS = 60 * 1000;
  var WEATHER_MS = 30 * 60 * 1000;
  var BAND_ROWS = 4;

  var state = {
    data: window.WALL_BOOT || null,
    idx: 0,
    kpiIdx: 0,
    paused: false,
    secondsOnSlide: 0,
  };

  function esc(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function settings() {
    return (state.data && state.data.settings) || {
      rotation_seconds: 60,
      refresh_seconds: 300,
      show_weather: 1,
    };
  }

  // ------------------------------------------------------------------ shell

  function renderShell(root) {
    root.innerHTML =
      '<header class="wall-header">' +
      '  <div class="wall-brand">' +
      '    <span class="wall-title">Project Wall</span>' +
      '    <span class="wall-date" id="wall-date"></span>' +
      "  </div>" +
      '  <div class="wall-right">' +
      '    <div class="wall-weather" id="wall-weather"></div>' +
      '    <div class="wall-clock" id="wall-clock"></div>' +
      "  </div>" +
      "</header>" +
      '<section class="wall-band" id="wall-band"></section>' +
      '<section class="wall-kpi" id="wall-kpi"></section>' +
      '<section class="wall-carousel" id="wall-carousel"></section>' +
      '<footer class="wall-footer">' +
      '  <div class="wall-pips" id="wall-pips"></div>' +
      '  <div class="wall-progress"><div class="wall-progress-bar" id="wall-progress"></div></div>' +
      '  <button class="wall-pause" id="wall-pause" type="button">⏸</button>' +
      "</footer>";
    root.removeAttribute("aria-busy");

    document.getElementById("wall-pause").addEventListener("click", function () {
      state.paused = !state.paused;
      this.textContent = state.paused ? "▶" : "⏸";
      this.classList.toggle("is-paused", state.paused);
    });
  }

  // ------------------------------------------------------------------- band

  function bandList(rows, fmt) {
    if (!rows || !rows.length) {
      return '<div class="wall-empty">Nothing here 🎉</div>';
    }
    var html = rows.slice(0, BAND_ROWS).map(fmt).join("");
    if (rows.length > BAND_ROWS) {
      html += '<div class="wall-more">+' + (rows.length - BAND_ROWS) + " more</div>";
    }
    return html;
  }

  function chip(names) {
    if (!names || !names.length) return '<span class="wall-chip wall-chip-none">Unassigned</span>';
    return names
      .map(function (n) {
        return '<span class="wall-chip">' + esc(n) + "</span>";
      })
      .join("");
  }

  function taskRow(task) {
    return (
      '<div class="wall-row prio-' + esc(String(task.priority || "low").toLowerCase()) + '">' +
      '<span class="wall-row-subject">' + esc(task.subject) + "</span>" +
      (task.project_label ? '<span class="wall-row-project">' + esc(task.project_label) + "</span>" : "") +
      '<span class="wall-row-people">' + chip(task.assignees) + "</span>" +
      "</div>"
    );
  }

  function overdueRow(task) {
    return (
      '<div class="wall-row wall-row-overdue">' +
      '<span class="wall-row-subject">' + esc(task.subject) + "</span>" +
      '<span class="wall-row-late">' + esc(task.days_overdue) + "d late</span>" +
      (task.project_label ? '<span class="wall-row-project">' + esc(task.project_label) + "</span>" : "") +
      "</div>"
    );
  }

  function eventRow(event) {
    var time = "All day";
    if (!event.all_day && event.starts_on) {
      var d = new Date(event.starts_on.replace(" ", "T"));
      if (!isNaN(d)) time = d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
    }
    return (
      '<div class="wall-row">' +
      '<span class="wall-row-time">' + esc(time) + "</span>" +
      '<span class="wall-row-subject">' + esc(event.subject) + "</span>" +
      "</div>"
    );
  }

  function renderBand() {
    var d = state.data;
    document.getElementById("wall-band").innerHTML =
      '<div class="wall-panel">' +
      '  <div class="wall-panel-head">📋 Today\'s Tasks <span>' + (d.today_tasks || []).length + "</span></div>" +
      bandList(d.today_tasks, taskRow) +
      "</div>" +
      '<div class="wall-panel wall-panel-warn">' +
      '  <div class="wall-panel-head">⚠️ Overdue / At Risk <span>' + (d.overdue_tasks || []).length + (d.overdue_overflow ? "+" : "") + "</span></div>" +
      bandList(d.overdue_tasks, overdueRow) +
      "</div>" +
      '<div class="wall-panel">' +
      '  <div class="wall-panel-head">📅 Today\'s Schedule <span>' + (d.events || []).length + "</span></div>" +
      bandList(d.events, eventRow) +
      "</div>";
  }

  // ------------------------------------------------------------------- kpi

  function kpiStatusClass(status) {
    var s = String(status || "").toLowerCase();
    return s ? "kpi-" + s : "";
  }

  function kpiTrend(pct) {
    if (pct == null) return "";
    var r = Math.round(pct * 10) / 10;
    if (r > 0) return "▲ " + r + "%";
    if (r < 0) return "▼ " + Math.abs(r) + "%";
    // 0 == flat OR no prior snapshot (Float can't be null) — show nothing.
    return "";
  }

  // Latest KPI snapshot per department, rotated one department at a time in
  // lock-step with the carousel. Empty/absent payload renders nothing (the
  // section collapses), so a disabled or hiccuping KPI feed never disturbs
  // the rest of the wall.
  function renderKpi() {
    var el = document.getElementById("wall-kpi");
    if (!el) return;
    var blocks = (state.data && state.data.kpi) || [];
    if (!blocks.length) {
      el.innerHTML = "";
      return;
    }
    var block = blocks[state.kpiIdx % blocks.length];
    var cards = (block.values || [])
      .slice(0, 8)
      .map(function (v) {
        return (
          '<div class="wall-kpi-card ' + kpiStatusClass(v.status) + '">' +
          '<div class="wall-kpi-val">' + esc(v.value_text) + "</div>" +
          '<div class="wall-kpi-label">' + esc(v.label) + "</div>" +
          '<div class="wall-kpi-trend">' + esc(kpiTrend(v.trend_pct)) + "</div>" +
          "</div>"
        );
      })
      .join("");
    el.innerHTML =
      '<div class="wall-kpi-head">' + esc(block.department) + " KPIs" +
      '<span class="wall-kpi-date">' + esc(block.snapshot_date) + "</span></div>" +
      '<div class="wall-kpi-grid">' + cards + "</div>";
  }

  // --------------------------------------------------------------- carousel

  function donut(stats) {
    var total = (stats && stats.total) || 0;
    var completed = (stats && stats.completed) || 0;
    var pct = total ? Math.round((completed / total) * 100) : 0;
    return (
      '<div class="wall-donut">' +
      '<svg viewBox="0 0 120 120" role="img" aria-label="Task completion">' +
      '<circle class="wall-donut-track" cx="60" cy="60" r="50" pathLength="100"></circle>' +
      '<circle class="wall-donut-fill" cx="60" cy="60" r="50" pathLength="100" ' +
      'stroke-dasharray="' + pct + " " + (100 - pct) + '"></circle>' +
      '<text x="60" y="58" class="wall-donut-pct">' + pct + "%</text>" +
      '<text x="60" y="76" class="wall-donut-sub">' + completed + " / " + total + " tasks</text>" +
      "</svg>" +
      "</div>"
    );
  }

  function renderSlide() {
    var projects = (state.data && state.data.top_projects) || [];
    var holder = document.getElementById("wall-carousel");
    if (!projects.length) {
      holder.innerHTML = '<div class="wall-empty wall-empty-big">No ranked active projects.</div>';
      renderPips(0, 0);
      return;
    }
    state.idx = state.idx % projects.length;
    var p = projects[state.idx];
    var stats = (state.data.task_stats || {})[p.name] || { total: 0, completed: 0, pending: 0 };

    holder.innerHTML =
      '<div class="wall-slide">' +
      '  <div class="wall-slide-info">' +
      '    <div class="wall-rank">★ ' + esc(p.rank) + "</div>" +
      '    <h2 class="wall-project">' + esc(p.project_name || p.name) + "</h2>" +
      '    <div class="wall-leads">' +
      (p.pm ? '<span class="wall-lead"><label>PM</label>' + esc(p.pm) + "</span>" : "") +
      (p.tech_lead ? '<span class="wall-lead"><label>Tech Lead</label>' + esc(p.tech_lead) + "</span>" : "") +
      "    </div>" +
      '    <div class="wall-meter"><div class="wall-meter-fill" style="width:' +
      Math.max(0, Math.min(100, p.percent_complete || 0)) +
      '%"></div></div>' +
      '    <div class="wall-meter-label">' + esc(p.percent_complete || 0) + "% complete · " +
      esc(stats.pending) + " open tasks</div>" +
      "  </div>" +
      donut(stats) +
      "</div>";

    renderPips(projects.length, state.idx);
  }

  function renderPips(count, active) {
    var holder = document.getElementById("wall-pips");
    var html = "";
    for (var i = 0; i < count; i++) {
      html +=
        '<button class="wall-pip' + (i === active ? " is-active" : "") + '" data-idx="' + i + '"></button>';
    }
    holder.innerHTML = html;
    holder.querySelectorAll(".wall-pip").forEach(function (pip) {
      pip.addEventListener("click", function () {
        state.idx = parseInt(this.getAttribute("data-idx"), 10) || 0;
        state.secondsOnSlide = 0;
        renderSlide();
      });
    });
  }

  function rotationTick() {
    var rotation = settings().rotation_seconds || 60;
    if (!state.paused) {
      state.secondsOnSlide += 1;
      if (state.secondsOnSlide >= rotation) {
        state.secondsOnSlide = 0;
        state.idx += 1;
        renderSlide();
        var kpiBlocks = (state.data && state.data.kpi) || [];
        if (kpiBlocks.length) {
          state.kpiIdx += 1;
          renderKpi();
        }
      }
    }
    var bar = document.getElementById("wall-progress");
    if (bar) bar.style.width = Math.min(100, (state.secondsOnSlide / rotation) * 100) + "%";
  }

  // ------------------------------------------------------------------ clock

  function tickClock() {
    var now = new Date();
    var clock = document.getElementById("wall-clock");
    var date = document.getElementById("wall-date");
    if (clock) clock.textContent = now.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
    if (date)
      date.textContent = now.toLocaleDateString([], {
        weekday: "long",
        month: "long",
        day: "numeric",
      });
  }

  // ---------------------------------------------------------------- weather

  // WMO weather codes → glyph + label (ported from Triton's WeatherWidget).
  function wmo(code) {
    if (code === 0) return ["☀️", "Clear"];
    if (code <= 2) return ["🌤", "Partly cloudy"];
    if (code === 3) return ["☁️", "Overcast"];
    if (code === 45 || code === 48) return ["🌫", "Fog"];
    if (code >= 51 && code <= 57) return ["🌦", "Drizzle"];
    if (code >= 61 && code <= 67) return ["🌧", "Rain"];
    if (code >= 71 && code <= 77) return ["🌨", "Snow"];
    if (code >= 80 && code <= 82) return ["🌧", "Showers"];
    if (code === 85 || code === 86) return ["🌨", "Snow showers"];
    if (code >= 95) return ["⛈", "Thunderstorm"];
    return ["🌡", ""];
  }

  function refreshWeather() {
    var s = settings();
    var holder = document.getElementById("wall-weather");
    if (!holder) return;
    if (!s.show_weather) {
      holder.innerHTML = "";
      return;
    }
    var url =
      "https://api.open-meteo.com/v1/forecast?latitude=" + encodeURIComponent(s.weather_latitude) +
      "&longitude=" + encodeURIComponent(s.weather_longitude) +
      "&current=temperature_2m,weather_code&daily=temperature_2m_max,temperature_2m_min" +
      "&temperature_unit=fahrenheit&timezone=auto&forecast_days=1";
    fetch(url)
      .then(function (r) { return r.json(); })
      .then(function (data) {
        var current = data.current || {};
        var daily = data.daily || {};
        var glyph = wmo(current.weather_code);
        holder.innerHTML =
          '<span class="wall-weather-icon">' + glyph[0] + "</span>" +
          '<span class="wall-weather-temp">' + Math.round(current.temperature_2m) + "°</span>" +
          '<span class="wall-weather-meta">' +
          esc(s.weather_label || "") +
          (daily.temperature_2m_max
            ? " · H " + Math.round(daily.temperature_2m_max[0]) + "° / L " + Math.round(daily.temperature_2m_min[0]) + "°"
            : "") +
          "</span>";
      })
      .catch(function () { /* keep the previous reading */ });
  }

  // ------------------------------------------------------------------- data

  function refreshData() {
    fetch(DATA_ENDPOINT, {
      method: "GET",
      credentials: "same-origin",
      headers: { Accept: "application/json", "X-Frappe-Site-Name": window.location.hostname },
    })
      .then(function (res) {
        if (res.status === 401 || res.status === 403) {
          // Session died — reload bounces through /login (redirect-to=/wall).
          window.location.reload();
          throw new Error("session expired");
        }
        return res.json();
      })
      .then(function (payload) {
        var data = payload && payload.message;
        if (!data) return;
        // Belt 2 of deploy pickup: server build moved on → reload onto it.
        if (data.deploy_version && window.WALL_BUILD && data.deploy_version !== window.WALL_BUILD) {
          window.location.reload();
          return;
        }
        state.data = data;
        renderBand();
        renderKpi();
        renderSlide();
      })
      .catch(function (err) {
        console.warn("Wall refresh failed:", err);
      });
  }

  // --------------------------------------------------------- service worker

  function registerWorker() {
    if (!("serviceWorker" in navigator)) return;
    navigator.serviceWorker
      .register("/wall-sw.js?v=" + encodeURIComponent(window.WALL_BUILD || "dev"))
      .then(function (reg) {
        setInterval(function () {
          reg.update().catch(function () {});
        }, SW_CHECK_MS);
      })
      .catch(function (err) {
        console.warn("Wall SW registration failed:", err);
      });

    // New worker took control after a deploy → reload immediately; a display
    // has no in-progress user input to protect (unlike the kiosk).
    var reloaded = false;
    navigator.serviceWorker.addEventListener("controllerchange", function () {
      if (reloaded) return;
      reloaded = true;
      window.location.reload();
    });
  }

  // ------------------------------------------------------------------- boot

  function boot() {
    var root = document.getElementById("wall-root");
    if (!root) return;
    renderShell(root);
    tickClock();
    if (state.data) {
      renderBand();
      renderKpi();
      renderSlide();
    }
    refreshWeather();
    registerWorker();

    setInterval(tickClock, 1000);
    setInterval(rotationTick, 1000);
    setInterval(refreshWeather, WEATHER_MS);
    setInterval(refreshData, (settings().refresh_seconds || 300) * 1000);
    document.addEventListener("visibilitychange", function () {
      if (!document.hidden) refreshData();
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
