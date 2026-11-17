/**
 * SAAANS dashboard: polls the Flask API and draws the readings.
 * Readings are only shown as current when the board really is sending them.
 */

const WINDOW_MS = 15 * 60 * 1000;
const STALE_AFTER_S = 30;
const GAP_MS = 30 * 1000;   // break the trace rather than join across missing readings
const SVG_NS = 'http://www.w3.org/2000/svg';

const BANDS = [
    [50, 'Good'], [100, 'Satisfactory'], [150, 'Moderate'],
    [200, 'Poor'], [300, 'Very poor'], [500, 'Hazardous'], [850, 'Above scale']
];

// Alert level = the point the old dashboard flagged as moderate
// Gas index bands. 100 = the room's usual air (R0 is the median sensor resistance)
const GAS_BANDS = [[80, 'Cleaner than usual'], [150, 'Usual for this room'], [250, 'Raised'], [Infinity, 'High']];
const GAS_SCALE_MAX = 400;
const MQ_SETTLE_MIN = 120;

const $ = id => document.getElementById(id);
let state = 'waiting';
let status = {};
let history = [];
let lastTimestamp = null;

document.addEventListener('DOMContentLoaded', () => {
    $('btnConnect').addEventListener('click', toggleConnection);
    $('btnExport').addEventListener('click', () => { window.location.href = '/api/export'; });
    window.addEventListener('resize', drawCharts);
    fetchPorts();
    tick();
    setInterval(tick, 2000);
    setInterval(fetchPorts, 10000);
    fetchForecast();
    setInterval(fetchForecast, 30000);
    fetchForecastSeries();
    setInterval(fetchForecastSeries, 30000);
    window.addEventListener('resize', () => drawForecastChart(forecastSeries));
    $('fcRefresh').addEventListener('click', retrainForecast);
    document.querySelectorAll('.range').forEach(b => b.addEventListener('click', () => setRange(Number(b.dataset.minutes))));
    setInterval(async () => { if (rangeMin !== 15) { await loadTrend(); drawRecorder(); } }, 60000);
});

async function tick() {
    try {
        const [statusRes, dataRes] = await Promise.all([fetch('/api/status'), fetch('/api/data')]);
        status = await statusRes.json();
        const d = await dataRes.json();
        state = readState(status);
        renderLink();
        renderCurrent(d);
        document.body.classList.remove('loading');
        if (d.timestamp !== lastTimestamp) {
            lastTimestamp = d.timestamp;
            await fetchHistory();
        }
    } catch (e) {
        state = 'noserver';
        renderLink();
        renderCurrent(null);
        document.body.classList.remove('loading');
    }
}

function readState(s) {
    if (s.is_simulated) return 'simulated';
    if (s.last_data_age_s == null) return s.is_connected ? 'waiting' : 'offline';
    if (!s.is_connected) return 'disconnected';
    return s.last_data_age_s > STALE_AFTER_S ? 'stale' : 'live';
}

function renderLink() {
    const age = status.last_data_age_s == null ? '' : Math.round(status.last_data_age_s);
    const labels = {
        live: [`live`, `${status.port}, last reading ${age} s ago`],
        waiting: [`warn`, `${status.port} open, waiting for data`],
        stale: [`warn`, `No data from ${status.port} for ${age} s`],
        disconnected: [`off`, `Board disconnected, last reading ${age} s ago`],
        offline: [`off`, `Board not connected`],
        simulated: [`warn`, `Simulated data, board not connected`],
        noserver: [`off`, `Server not responding`]
    };
    const [cls, text] = labels[state];
    $('linkLed').className = `led ${cls}`;
    $('linkLabel').textContent = text;

    const connected = !!status.is_connected;
    $('btnConnect').textContent = connected ? 'Disconnect' : 'Connect';
    $('btnConnect').dataset.connected = connected;
    $('pathPort').textContent = `${status.port || 'COM port'}, ${status.baudrate || 115200} baud`;
}

function renderCurrent(d) {
    const hasValues = d && ['live', 'stale', 'disconnected', 'simulated'].includes(state);
    renderHero(hasValues ? d : null);
    renderSensors(hasValues ? d : null);
    renderPmsDetail(hasValues ? d : null);
    renderGasIndex(hasValues ? d : null);
}

function renderHero(d) {
    const verdict = $('verdict');
    const advice = $('advice');
    // The AQI number is the page's focal point. It shows only when there are particle readings behind it.
    const hasAqi = d && d.pm25_est != null && d.aqi != null;
    $('aqiNow').textContent = hasAqi ? Math.round(d.aqi) : '--';
    $('aqiCat').textContent = hasAqi ? d.aqi_category : '';
    // Category swatch carries the severity colour; the number stays gold, which reads on black at every level
    $('aqiCat').style.setProperty('--swatch', hasAqi && d.aqi_color ? d.aqi_color : 'transparent');
    const messages = {
        waiting: ['Waiting for the first reading', 'The port is open. The ESP32 sends a new line every 5 seconds.'],
        offline: ['Board not connected', 'Plug in the ESP32. The dashboard connects by itself when it finds the board.'],
        noserver: ['Server not responding', 'Start iaq_server.py, then reload this page.']
    };
    if (!d) {
        const [title, text] = messages[state] || messages.waiting;
        verdict.textContent = title;
        advice.textContent = text;
        return;
    }
    if (state === 'simulated') {
        verdict.textContent = 'Simulated data';
        advice.textContent = 'These numbers are generated, not measured. Connect the ESP32 to see real readings.';
        return;
    }
    if (d.pm25_est == null) {
        verdict.textContent = 'PM sensor not sending data';
        advice.textContent = 'No air quality verdict without particle readings. Check the PMS5003T wires: 5V, GND and TX to RX2.';
        return;
    }
    const category = d.aqi_category.toLowerCase();
    verdict.textContent = state === 'live'
        ? `The air in this room is ${category}`
        : `Last reading: ${category}`;
    advice.textContent = state === 'live'
        ? d.ventilation_message
        : 'The board has stopped sending data, so this may be out of date.';
}

function renderSensors(d) {
    const fmt = (v, digits = 0) => (d && v != null ? Number(v).toFixed(digits) : '--');
    $('pm25').textContent = fmt(d && d.pm25_est);
    $('pm25epa').textContent = fmt(d && d.pm25_epa);
    $('pm1').textContent = fmt(d && d.pm1);
    $('pm10').textContent = fmt(d && d.pm10_est);
    $('temp').textContent = fmt(d && d.temperature, 1);
    $('hum').textContent = fmt(d && d.humidity, 1);
    $('press').textContent = fmt(d && d.pressure, 1);
    $('mqRaw').textContent = fmt(d && d.mq_raw);
    $('mqVolt').textContent = fmt(d && d.voltage, 3);
    $('mqR0').textContent = status.r0 != null ? Number(status.r0).toFixed(1) : '--';

    const fresh = state === 'live';
    setTag('pmTag', !d ? ['No data', ''] : d.pm25_est == null ? ['No frames', 'bad'] : tagFor(fresh));
    setTag('bmeTag', !d ? ['No data', ''] : tagFor(fresh));
    setTag('mqTag', !d ? ['No data', ''] : status.calibrated ? tagFor(fresh) : ['Not calibrated', 'warn']);
    $('mqNote').textContent = status.calibrated
        ? 'Gas sensor. One combined signal, not a gas-by-gas reading'
        : 'Gas sensor. Needs 24 to 48 hours powered on, then calibration in clean air';
}

function renderPmsDetail(d) {
    const has = d && d.n03 != null;
    const text = (id, v, digits = 0) => { $(id).textContent = has && v != null ? Number(v).toFixed(digits) : '--'; };
    const box = $('counts');
    box.innerHTML = '';
    const sizes = [['> 0.3 µm', 'n03'], ['> 0.5 µm', 'n05'], ['> 1.0 µm', 'n10'], ['> 2.5 µm', 'n25']];
    const top = has ? Math.max(d.n03, 1) : 1;
    sizes.forEach(([label, key]) => {
        const row = document.createElement('div');
        row.className = 'count-row';
        row.innerHTML = '<span></span><div class="meter"><span></span></div><b></b>';
        row.firstChild.textContent = label;
        row.children[1].firstChild.style.width = has ? `${(d[key] / top) * 100}%` : '0';
        row.lastChild.textContent = has ? d[key] : '--';
        box.appendChild(row);
    });
    text('atm1', d && d.pm1); text('atm25', d && d.pm25_est); text('atm10', d && d.pm10_est);
    text('cf1', d && d.cf_pm1); text('cf25', d && d.cf_pm25); text('cf10', d && d.cf_pm10);
    text('epa25', d && d.pm25_epa);
    text('pmsT', d && d.pms_temp, 1); text('pmsH', d && d.pms_hum, 1);
    // The comparison shows the raw BME280 value, so the offset applied elsewhere doesn't hide the gap
    const bmeRaw = d && (d.temperature_raw != null ? d.temperature_raw : d.temperature);
    text('bmeT', bmeRaw, 1); text('bmeH', d && d.humidity, 1);
    text('diffT', has ? d.pms_temp - bmeRaw : null, 1);
    text('diffH', has ? d.pms_hum - d.humidity : null, 1);
    $('pmsHealth').textContent = !has ? 'No data yet'
        : d.pms_error === 0 ? `Firmware version 0x${d.pms_version.toString(16).toUpperCase()}, no sensor error`
        : `Sensor reports error code ${d.pms_error}`;
}

function tagFor(fresh) {
    if (state === 'simulated') return ['Simulated', 'warn'];
    return fresh ? ['Reading', 'ok'] : ['Out of date', 'warn'];
}

function setTag(id, [text, cls]) {
    $(id).textContent = text;
    $(id).className = `tag ${cls}`;
    // A sensor tag only earns its place when something is wrong; "Reading" on all three is noise
    $(id).hidden = cls === 'ok' && id !== 'fcTag';
}

function renderGasIndex(d) {
    const val = d && d.gas_index != null ? d.gas_index : null;
    const warmMin = status.mq_powered_min;
    const warming = val != null && warmMin != null && warmMin < MQ_SETTLE_MIN;
    $('gasIndex').textContent = val == null ? '--' : Math.round(val);
    $('gasIndex').classList.toggle('settling', warming);
    const band = val == null ? null : GAS_BANDS.find(([top]) => val < top);
    $('gasBand').textContent = val == null ? 'No data'
        : warming ? `Heater warming up, ${warmMin} of about ${MQ_SETTLE_MIN} min. Readings swing until then.`
        : band[1];
    $('gasBand').classList.toggle('raised', !warming && val != null && val >= 150);
    const mark = $('gasMark');
    mark.hidden = val == null;
    if (val != null) mark.style.left = `${Math.min(100, (val / GAS_SCALE_MAX) * 100)}%`;
    const tag = $('gasTag');
    tag.hidden = status.calibrated !== false;
    tag.textContent = 'Not calibrated';
    tag.className = 'tag warn';
}

async function fetchHistory() {
    const res = await fetch('/api/history');
    if (!res.ok) return;
    history = (await res.json()).history || [];
    drawCharts();
    renderLog();
}

function drawCharts() {
    drawRecorder();
    drawSpark('sparkPm', 'pm25_est');
    drawSpark('sparkTemp', 'temperature');
    drawSpark('sparkMq', 'mq_raw');
    drawSpark('sparkGas', 'gas_index');
}

function el(parent, name, attrs = {}, text) {
    const node = document.createElementNS(SVG_NS, name);
    Object.entries(attrs).forEach(([k, v]) => node.setAttribute(k, v));
    if (text != null) node.textContent = text;
    parent.appendChild(node);
    return node;
}

let rangeMin = 15;
let trend = { points: [], bucket_min: 1 };
const rangeLabel = m => ({ 15: '15 minutes', 60: 'hour', 360: '6 hours', 1440: '24 hours' }[m]);

// Switch the chart range. Ranges above 15 minutes are loaded from the log, then refreshed with the page's other polling.
async function setRange(minutes) {
    rangeMin = minutes;
    document.querySelectorAll('.range').forEach(b => b.setAttribute('aria-pressed', String(Number(b.dataset.minutes) === minutes)));
    $('recorder').setAttribute('aria-label', `Air quality index over the last ${rangeLabel(minutes)}`);
    $('recorderNote').textContent = minutes === 15 ? 'Live, a point about every 10 seconds'
        : `Averaged from the log, one point per ${minutes === 60 ? 'minute' : minutes === 360 ? '5 minutes' : '15 minutes'}`;
    await loadTrend();
    drawRecorder();
}

async function loadTrend() {
    if (rangeMin === 15) return;
    try {
        const res = await fetch(`/api/trend?minutes=${rangeMin}`);
        if (res.ok) trend = await res.json();
    } catch (e) {
        console.error('Could not load the trend:', e);
    }
}

function drawRecorder() {
    const svg = $('recorder');
    const { width: W, height: H } = svg.getBoundingClientRect();
    if (!W) return;
    svg.innerHTML = '';
    svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
    svg.classList.toggle('live-pen', state === 'live');

    const narrow = W < 600;
    const m = { l: narrow ? 34 : 44, r: narrow ? 14 : 96, t: 16, b: 26 };
    const plotW = W - m.l - m.r;
    const plotH = H - m.t - m.b;
    const now = Date.now();
    const windowMs = rangeMin * 60 * 1000;
    const start = now - windowMs;
    // 15 min is the live trace; longer ranges come from the log, averaged into buckets
    const points = (rangeMin === 15
        ? history.filter(h => h.pm25_est != null)   // AQI without particle data would read falsely low
            .map(h => ({ t: new Date(h.timestamp).getTime(), v: h.aqi }))
        : trend.points.map(([t, v]) => ({ t: new Date(t).getTime(), v })))
        .filter(p => p.t >= start && p.v != null);
    const gapMs = rangeMin === 15 ? GAP_MS : Math.max(3, trend.bucket_min * 3) * 60 * 1000;

    const peak = Math.max(0, ...points.map(p => p.v));
    const yMax = peak > 500 ? Math.min(850, Math.ceil((peak + 20) / 50) * 50) : (peak > 300 ? 500 : 300);
    const x = t => m.l + ((t - start) / windowMs) * plotW;
    const y = v => m.t + plotH - (v / yMax) * plotH;

    // AQI category limits, named by the band below each line
    let lower = 0;
    el(svg, 'line', { class: 'grid', x1: m.l, x2: m.l + plotW, y1: y(0), y2: y(0) });
    el(svg, 'text', { x: m.l - 6, y: y(0) + 4, 'text-anchor': 'end' }, '0');
    BANDS.forEach(([limit, name]) => {
        if (lower >= yMax) return;
        const top = Math.min(limit, yMax);
        el(svg, 'line', { class: 'limit', x1: m.l, x2: m.l + plotW, y1: y(top), y2: y(top) });
        el(svg, 'text', { x: m.l - 6, y: y(top) + 4, 'text-anchor': 'end' }, top);
        if (!narrow) el(svg, 'text', { x: m.l + plotW + 8, y: (y(lower) + y(top)) / 2 + 4 }, name);
        lower = limit;
    });

    // Time grid on the clock: 5 min, 10 min, 1 h or 4 h apart, depending on the range
    const step = ({ 15: 5, 60: 10, 360: 60, 1440: 240 }[rangeMin]) * 60 * 1000;
    const zoneShift = new Date().getTimezoneOffset() * 60 * 1000;   // line up on local clock times
    for (let t = Math.ceil((start - zoneShift) / step) * step + zoneShift; t <= now; t += step) {
        el(svg, 'line', { class: 'grid', x1: x(t), x2: x(t), y1: m.t, y2: m.t + plotH });
        const label = new Date(t).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
        el(svg, 'text', { x: x(t), y: H - 8, 'text-anchor': 'middle' }, label);
    }

    if (!points.length) {
        el(svg, 'text', { class: 'empty', x: m.l + plotW / 2, y: m.t + plotH / 2, 'text-anchor': 'middle' }, `No readings in the last ${rangeLabel(rangeMin)}`);
        return;
    }

    let d = '';
    points.forEach((p, i) => {
        const gap = i === 0 || p.t - points[i - 1].t > gapMs;
        d += `${gap ? 'M' : 'L'}${x(p.t).toFixed(1)} ${y(p.v).toFixed(1)}`;
    });
    el(svg, 'path', { class: 'trace', d });

    // Pen tip with the current value riding on the trace
    const last = points[points.length - 1];
    const px = x(last.t);
    const py = y(last.v);
    el(svg, 'circle', { class: 'pen-ring', cx: px, cy: py, r: 9 });
    el(svg, 'circle', { class: 'pen', cx: px, cy: py, r: 4.5 });
    const label = `AQI ${Math.round(last.v)}`;
    const tagW = label.length * 8 + 14;
    const tagX = Math.max(m.l, Math.min(px - tagW - 10, W - tagW - 4));
    const tagY = Math.max(2, py - 28);
    el(svg, 'rect', { class: 'pen-tag', x: tagX, y: tagY, width: tagW, height: 22 });
    el(svg, 'text', { class: 'pen-text', x: tagX + tagW / 2, y: tagY + 16, 'text-anchor': 'middle' }, label);
}

function drawSpark(id, key) {
    const svg = $(id);
    const { width: W, height: H } = svg.getBoundingClientRect();
    if (!W) return;
    svg.innerHTML = '';
    svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
    const start = Date.now() - WINDOW_MS;
    const pts = history
        .map(h => ({ t: new Date(h.timestamp).getTime(), v: h[key] }))
        .filter(p => p.t >= start && p.v != null);
    if (pts.length < 2) return;
    const lo = Math.min(...pts.map(p => p.v));
    const hi = Math.max(...pts.map(p => p.v));
    const span = hi - lo || 1;
    const x = t => ((t - start) / WINDOW_MS) * (W - 2) + 1;
    const y = v => H - 4 - ((v - lo) / span) * (H - 8);
    let d = '';
    pts.forEach((p, i) => {
        const gap = i === 0 || p.t - pts[i - 1].t > GAP_MS;
        d += `${gap ? 'M' : 'L'}${x(p.t).toFixed(1)} ${y(p.v).toFixed(1)}`;
    });
    el(svg, 'path', { d });
}

function renderLog() {
    const body = $('logBody');
    body.innerHTML = '';
    history.slice(-8).reverse().forEach(h => {
        const tr = document.createElement('tr');
        const cells = [
            h.time_str, h.pm25_est == null ? '--' : Math.round(h.aqi), h.pm25_est ?? '--', h.pm10_est ?? '--',
            Number(h.temperature).toFixed(1), Number(h.humidity).toFixed(1),
            Number(h.pressure).toFixed(1), h.mq_raw, h.fan ? 'On' : 'Off'
        ];
        cells.forEach((value, i) => {
            const td = document.createElement('td');
            if (i > 0) td.className = 'num';
            td.textContent = value;
            tr.appendChild(td);
        });
        body.appendChild(tr);
    });
    $('logCount').textContent = `${status.total_readings || history.length} readings held, all saved to saans_log.csv`;
}

async function fetchForecast() {
    try {
        renderForecast(await (await fetch('/api/forecast')).json());
    } catch (e) {
        console.error('Could not load the forecast:', e);
    }
}

let forecastSeries = null;

const RETRAIN_MESSAGES = {
    promoted: 'New model is better and now live',
    promoted_first_model: 'New model saved',
    promoted_champion_unscorable: 'New model saved',
    kept_champion_better: 'Kept the current model, it scored better',
    kept_champion_new_failed: 'Kept the current model, the new one did not beat the baselines',
    withdrawn_no_skill: 'Neither model beats the baselines, so no forecast is shown',
    saved_no_skill_model: 'New model does not beat the baselines, so no forecast is shown',
};

// Retrain on all logged data in the background. The model is only replaced if it passes the same 5% gate as the nightly run.
async function retrainForecast() {
    const btn = $('fcRefresh');
    btn.disabled = true;
    btn.classList.add('busy');
    btn.textContent = 'Retraining';
    $('fcUpdated').textContent = 'Training on all logged data, about 20 seconds';
    try {
        await fetch('/api/forecast/retrain', { method: 'POST' });
        let status;
        do {
            await new Promise(r => setTimeout(r, 2000));
            status = await (await fetch('/api/forecast/retrain')).json();
        } while (status.running);
        $('fcUpdated').textContent = status.error ? `Retrain failed: ${status.error}`
            : (RETRAIN_MESSAGES[status.result && status.result.decision] || 'Retrained');
        await Promise.all([fetchForecast(), fetchForecastSeries()]);
    } catch (e) {
        console.error('Could not retrain the forecast:', e);
        $('fcUpdated').textContent = 'Could not reach the server';
    } finally {
        btn.disabled = false;
        btn.classList.remove('busy');
        btn.textContent = 'Retrain forecast';
    }
}

async function fetchForecastSeries() {
    try {
        forecastSeries = await (await fetch('/api/forecast/series')).json();
        drawForecastChart(forecastSeries);
    } catch (e) {
        console.error('Could not load the forecast graph:', e);
    }
}

// Measured PM2.5 for the last 3 hours, plus the model's forecast drawn at the time it points to.
function drawForecastChart(s) {
    const wrap = $('fcChartWrap');
    const show = s && (s.state === 'ready' || s.state === 'unverified') && s.measured.length > 1;
    wrap.hidden = !show;
    if (!show) return;
    const svg = $('fcChart');
    const { width: W, height: H } = svg.getBoundingClientRect();
    if (!W) return;
    svg.innerHTML = '';
    svg.setAttribute('viewBox', `0 0 ${W} ${H}`);

    const parse = rows => rows.map(([t, v]) => ({ t: new Date(t).getTime(), v }));
    const measured = parse(s.measured);
    const forecast = parse(s.forecast);
    const now = measured[measured.length - 1].t;
    const start = now - 3 * 3600 * 1000;
    const end = now + (s.horizon_min || 30) * 60 * 1000;
    const m = { l: 44, r: 14, t: 14, b: 26 };
    const plotW = W - m.l - m.r;
    const plotH = H - m.t - m.b;
    const peak = Math.max(...measured.map(p => p.v), ...forecast.map(p => p.v));
    const yMax = Math.ceil((peak * 1.1) / 50) * 50;
    const x = t => m.l + ((t - start) / (end - start)) * plotW;
    const y = v => m.t + plotH - (v / yMax) * plotH;

    for (let v = 0; v <= yMax; v += yMax > 300 ? 100 : 50) {
        el(svg, 'line', { class: 'grid', x1: m.l, x2: m.l + plotW, y1: y(v), y2: y(v) });
        el(svg, 'text', { x: m.l - 6, y: y(v) + 4, 'text-anchor': 'end' }, v);
    }
    const halfHour = 30 * 60 * 1000;
    for (let t = Math.ceil(start / halfHour) * halfHour; t <= end; t += halfHour) {
        const clock = new Date(t);
        if (clock.getMinutes() !== 0) continue;   // label full hours only
        el(svg, 'line', { class: 'grid', x1: x(t), x2: x(t), y1: m.t, y2: m.t + plotH });
        el(svg, 'text', { x: x(t), y: H - 8, 'text-anchor': 'middle' },
            clock.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false }));
    }
    // Future starts at "now"
    el(svg, 'rect', { class: 'future', x: x(now), y: m.t, width: x(end) - x(now), height: plotH });
    el(svg, 'text', { class: 'now', x: x(now) + 6, y: m.t + 12 }, 'now');

    const path = pts => pts.reduce((d, p, i) => {
        const gap = i === 0 || p.t - pts[i - 1].t > 3 * 60 * 1000;   // points are one minute apart
        return d + `${gap ? 'M' : 'L'}${x(p.t).toFixed(1)} ${y(p.v).toFixed(1)}`;
    }, '');
    el(svg, 'path', { class: 'measured', d: path(measured) });
    if (forecast.length) {
        el(svg, 'path', { class: 'predicted', d: path(forecast) });
        const tip = forecast[forecast.length - 1];
        // Typical error from the held-out test, drawn as a bar at the forecast tip
        const err = s.typical_error || 0;
        el(svg, 'line', { class: 'errbar', x1: x(tip.t), x2: x(tip.t), y1: y(Math.min(yMax, tip.v + err)), y2: y(Math.max(0, tip.v - err)) });
        el(svg, 'circle', { class: 'tip', cx: x(tip.t), cy: y(tip.v), r: 4.5 });
    }
}

function renderForecast(f) {
    const hours = f.hours_logged || 0;
    const m = f.metrics;
    const test = m && m.stage === 'pipeline_test';
    let headline, note, tag, pathNote;
    switch (f.state) {
        case 'ready':
            headline = `${Math.round(f.forecast.pm25)} µg/m³ at ${f.forecast.for_time.slice(11, 16)}`;
            note = `Predicted ${f.horizon_min} minutes ahead by the ${m.best_model.toLowerCase()}, from ${f.forecast.current_pm25} µg/m³ now.`;
            tag = test ? ['Pipeline test', 'warn'] : ['Live forecast', 'ok'];
            pathNote = test ? 'Running, still in pipeline test' : 'Running';
            break;
        case 'unverified':
            headline = `${Math.round(f.forecast.pm25)} µg/m³ at ${f.forecast.for_time.slice(11, 16)}`;
            note = `Unverified. It does not clearly beat just repeating the current value (${f.forecast.current_pm25} µg/m³ now), so treat it as a rough guide.`;
            tag = ['Unverified', 'warn'];
            pathNote = 'Running, unverified (not beating baseline)';
            break;
        case 'gap':
            headline = 'Waiting for 30 unbroken minutes';
            note = 'The model needs the last 30 minutes of readings. The log has a recent gap.';
            tag = ['Waiting', 'warn'];
            pathNote = 'Waiting for unbroken readings';
            break;
        case 'no_skill':
            headline = 'No forecast shown';
            note = 'The trained model does not clearly beat the simple baselines, so its numbers would mislead. Collect more varied data and train again.';
            tag = ['Not beating baseline', 'warn'];
            pathNote = 'Trained, not beating baseline yet';
            break;
        case 'no_model':
            headline = hours < f.hours_to_train ? 'Collecting training data' : 'Ready to train';
            note = hours < f.hours_to_train
                ? `No model yet. It will predict PM2.5 ${f.horizon_min} minutes ahead once there is enough real data.`
                : 'Run python saaans_forecast.py to train the model.';
            tag = ['No model yet', ''];
            pathNote = 'Collecting training data';
            break;
        default:
            headline = 'Forecast unavailable';
            note = f.message || '';
            tag = ['Unavailable', 'bad'];
            pathNote = 'Unavailable';
    }
    // Confidence: typical error and skill against "stays the same", from the held-out test
    const conf = $('fcConfidence');
    const showConf = f.confidence && (f.state === 'ready' || f.state === 'unverified');
    conf.hidden = !showConf;
    if (showConf) {
        const sign = f.skill_pct >= 0 ? '+' : '';
        conf.dataset.level = f.confidence;
        conf.textContent = `Confidence: ${f.confidence}. Typically off by ±${f.typical_error} µg/m³. ` +
            `Skill vs staying the same: ${sign}${f.skill_pct}% (needs +${f.skill_needed_pct}%).`;
    }
    $('fcHeadline').textContent = headline;
    $('fcNote').textContent = note;
    setTag('fcTag', tag);
    $('pathForecastNote').textContent = pathNote;
    $('pathForecast').classList.toggle('pending', f.state !== 'ready');

    const goal = f.hours_for_result || 72;
    $('fcMeter').firstElementChild.style.width = `${Math.min(100, (hours / goal) * 100)}%`;
    $('fcProgress').textContent = f.state === 'unavailable' ? '' :
        `${hours.toFixed(1)} of ${goal} h logged for a first real result`;

    $('fcScores').hidden = !m;
    if (m) {
        const body = $('fcBody');
        body.innerHTML = '';
        Object.entries(m.results).forEach(([name, r]) => {
            const tr = document.createElement('tr');
            if (name === m.best_model) tr.className = 'best';
            [name, r.mae, r.rmse, r.r2].forEach((value, i) => {
                const td = document.createElement('td');
                if (i > 0) td.className = 'num';
                td.textContent = value;
                tr.appendChild(td);
            });
            body.appendChild(tr);
        });
    }
}

async function fetchPorts() {
    try {
        const data = await (await fetch('/api/ports')).json();
        const select = $('portSelect');
        const current = status.port || select.value;
        select.innerHTML = '';
        const ports = data.ports && data.ports.length ? data.ports : [{ port: current }];
        ports.forEach(p => {
            const opt = document.createElement('option');
            opt.value = p.port;
            opt.textContent = p.port;
            opt.selected = p.port === current;
            select.appendChild(opt);
        });
    } catch (e) {
        console.error('Could not list ports:', e);
    }
}

async function toggleConnection() {
    const btn = $('btnConnect');
    const disconnecting = btn.dataset.connected === 'true';
    btn.disabled = true;
    try {
        await fetch('/api/connect', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(disconnecting
                ? { action: 'disconnect' }
                : { action: 'connect', port: $('portSelect').value, baudrate: 115200 })
        });
        await tick();
    } finally {
        btn.disabled = false;
    }
}
