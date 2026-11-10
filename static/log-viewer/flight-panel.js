/* Solo Web Utility flight log viewer: the panel (log list, summary, flight modes, charts,
 * messages, parameters). All state lives in the object returned by create(); nothing here
 * reads or writes the rest of the utility's page or state. Log text is only ever put on the
 * page with textContent. */
(function (LV) {
    'use strict';

    var BIG_LOG = 150 * 1024 * 1024;     // ask before loading a log this large into a phone
    var LIST_SHOW = 25;
    var PRESETS = [
        { label: 'Altitude', type: 'CTUN', fields: ['Alt', 'DAlt'] },
        { label: 'Climb rate', type: 'CTUN', fields: ['CRt', 'DCRt'] },
        { label: 'Roll', type: 'ATT', fields: ['Roll', 'DesRoll'] },
        { label: 'Pitch', type: 'ATT', fields: ['Pitch', 'DesPitch'] },
        { label: 'Yaw', type: 'ATT', fields: ['Yaw', 'DesYaw'] },
        { label: 'Battery voltage', type: 'BAT', fields: ['Volt'] },
        { label: 'Battery current', type: 'BAT', fields: ['Curr'] },
        { label: 'Throttle', type: 'CTUN', fields: ['ThO'] },
        { label: 'Vibration', type: 'VIBE', fields: ['VibeX', 'VibeY', 'VibeZ'] },
        { label: 'GPS satellites', type: 'GPS', fields: ['NSats'] },
        { label: 'GPS HDOP', type: 'GPS', fields: ['HDop'] },
        { label: 'Speed', type: 'GPS', fields: ['Spd'] }
    ];
    var MODE_COLORS = ['#3b82f6', '#f59e0b', '#10b981', '#a855f7', '#ef4444', '#14b8a6', '#eab308', '#ec4899'];

    function h(tag, attrs, kids) {
        var el = document.createElement(tag);
        if (attrs) Object.keys(attrs).forEach(function (k) {
            if (k === 'text') el.textContent = attrs[k];
            else if (k === 'cls') el.className = attrs[k];
            else el.setAttribute(k, attrs[k]);
        });
        (kids || []).forEach(function (c) { if (c) el.appendChild(c); });
        return el;
    }

    function mb(bytes) { return (bytes / 1048576).toFixed(bytes < 10485760 ? 1 : 0) + ' MB'; }
    function num(v, digits, unit) { return v === null || v === undefined || !isFinite(v) ? '–' : v.toFixed(digits) + (unit || ''); }
    function dur(s) {
        if (!s && s !== 0) return '–';
        var total = Math.round(s), m = Math.floor(total / 60), r = total - m * 60;
        return m + ' min ' + (r < 10 ? '0' : '') + r + ' s';
    }

    /* Fixed-height rows; only the rows in view are on the page, so 100,000 lines cost nothing. */
    function VirtualList(box, rowH, renderRow, guard) {
        var items = [], spacer = h('div', { cls: 'lv-vspacer' }), rows = h('div', { cls: 'lv-vrows' });
        box.appendChild(spacer);
        box.appendChild(rows);
        function paint() {
            var top = box.scrollTop, hgt = box.clientHeight || 300;
            var first = Math.max(0, Math.floor(top / rowH) - 5), last = Math.min(items.length, Math.ceil((top + hgt) / rowH) + 5);
            rows.style.transform = 'translateY(' + (first * rowH) + 'px)';
            rows.textContent = '';
            for (var k = first; k < last; k++) rows.appendChild(renderRow(items[k], k));
        }
        box.addEventListener('scroll', guard(paint));
        return {
            set: function (list) { items = list; spacer.style.height = (list.length * rowH) + 'px'; box.scrollTop = 0; paint(); },
            paint: paint
        };
    }

    LV.parts.FlightPanel = {
        create: function (root, env) {
            var api = LV.parts.api, guard = env.guard;
            var worker = null, reqId = 0, pending = {}, summary = null, chart = null;
            var state = { logs: [], showAll: false, armed: false, current: null, sev: 'all', filter: '', pfilter: '', tab: 'charts', picked: [] };

            /* ---------- layout ---------- */
            var status = h('div', { cls: 'lv-status' });
            var list = h('div', { cls: 'lv-list' });
            var btnRefresh = h('button', { cls: 'lv-btn', type: 'button', text: 'Refresh' });
            var fileInput = h('input', { type: 'file', accept: '.bin,.BIN' });
            var fileLabel = h('label', { cls: 'lv-btn lv-file' }, [document.createTextNode('Open a .BIN file…'), fileInput]);
            var plot = h('a', { cls: 'lv-link', href: 'https://plot.ardupilot.org', target: '_blank', rel: 'noopener', text: 'UAV Log Viewer at plot.ardupilot.org (needs internet) ↗' });

            var viewTitle = h('h3', { text: '' });
            var btnClose = h('button', { cls: 'lv-btn', type: 'button', text: 'Close' });
            var progress = h('div', { cls: 'lv-progress' });
            var tiles = h('div', { cls: 'lv-tiles' });
            var timeline = h('div', { cls: 'lv-timeline' });
            var tabBtns = {};
            var tabs = h('div', { cls: 'lv-tabs' }, ['charts', 'messages', 'params'].map(function (k) {
                var b = h('button', { cls: 'lv-tab', type: 'button', text: { charts: 'Charts', messages: 'Messages', params: 'Parameters' }[k] });
                b.addEventListener('click', guard(function () { showTab(k); }));
                tabBtns[k] = b;
                return b;
            }));

            // charts
            var presetBox = h('div', { cls: 'lv-presets' });
            var typeSel = h('select', { cls: 'lv-input' });
            var fieldSel = h('select', { cls: 'lv-input' });
            var btnAdd = h('button', { cls: 'lv-btn', type: 'button', text: 'Add to chart' });
            var btnClear = h('button', { cls: 'lv-btn', type: 'button', text: 'Clear' });
            var btnReset = h('button', { cls: 'lv-btn', type: 'button', text: 'Reset zoom' });
            btnReset.hidden = true;
            var canvas = h('canvas', { cls: 'lv-canvas' });
            var legend = h('div', { cls: 'lv-legend' });
            var paneCharts = h('div', { cls: 'lv-pane' }, [
                presetBox,
                h('div', { cls: 'lv-row' }, [typeSel, fieldSel, btnAdd, btnClear, btnReset]),
                canvas, legend,
                h('p', { cls: 'lv-hint', text: 'Drag across the chart to zoom in. Tap or hover for values. Dashed lines are flight mode changes; the green band is armed time.' })
            ]);

            // messages
            var sevSel = h('select', { cls: 'lv-input' });
            [['all', 'All'], ['warning', 'Warnings and errors'], ['error', 'Errors only']].forEach(function (o) {
                sevSel.appendChild(h('option', { value: o[0], text: o[1] }));
            });
            var msgFilter = h('input', { cls: 'lv-input lv-grow', type: 'search', placeholder: 'Filter (text or regular expression)' });
            var msgCount = h('span', { cls: 'lv-count' });
            var msgBox = h('div', { cls: 'lv-vlist' });
            var paneMessages = h('div', { cls: 'lv-pane' }, [h('div', { cls: 'lv-row' }, [sevSel, msgFilter, msgCount]), msgBox]);

            // parameters
            var parFilter = h('input', { cls: 'lv-input lv-grow', type: 'search', placeholder: 'Filter parameters (e.g. BATT, PLND_)' });
            var parCount = h('span', { cls: 'lv-count' });
            var parBox = h('div', { cls: 'lv-vlist' });
            var paneParams = h('div', { cls: 'lv-pane' }, [
                h('div', { cls: 'lv-row' }, [parFilter, parCount]), parBox,
                h('p', { cls: 'lv-hint', text: 'Parameter values recorded in this log (the last value if one changed during the flight).' })
            ]);

            var view = h('div', { cls: 'lv-view' }, [
                h('div', { cls: 'lv-viewhead' }, [viewTitle, btnClose]), progress, tiles, timeline, tabs,
                paneCharts, paneMessages, paneParams
            ]);
            view.hidden = true;

            root.appendChild(h('div', { cls: 'lv-card' }, [
                h('div', { cls: 'lv-head' }, [h('h2', { text: '📈 Flight Logs' }), h('span', { cls: 'lv-badge', text: 'Review after landing' })]),
                h('p', { cls: 'lv-desc', text: "The Cube's flight logs saved on the Solo (/log/dataflash). Download a log, or View it here: summary, flight modes, messages, parameters and charts. Works on SoloLink Wi-Fi without internet." }),
                h('div', { cls: 'lv-row' }, [btnRefresh, fileLabel, plot]),
                status, list, view
            ]));

            var msgList = VirtualList(msgBox, 24, function (m) {
                var row = h('div', { cls: 'lv-vrow lv-sev-' + m.sev });
                row.appendChild(h('span', { cls: 'lv-t', text: LV.parts.fmtTime(Math.max(0, m.t)) }));
                row.appendChild(h('span', { cls: 'lv-kind', text: m.kind }));
                row.appendChild(h('span', { cls: 'lv-text', text: m.text }));
                return row;
            }, guard);
            var parList = VirtualList(parBox, 24, function (p) {
                var row = h('div', { cls: 'lv-vrow' });
                row.appendChild(h('span', { cls: 'lv-pname', text: p.name }));
                row.appendChild(h('span', { cls: 'lv-text', text: typeof p.value === 'number' ? String(+p.value.toPrecision(7)) : String(p.value) }));
                return row;
            }, guard);
            chart = LV.parts.Chart(canvas, legend, guard, function (zoomed) { btnReset.hidden = !zoomed; });

            /* ---------- worker ---------- */
            function call(msg, transfer) {
                if (!worker) {
                    worker = new Worker(env.base + 'dataflash-worker.js' + env.q);
                    worker.onmessage = guard(function (ev) {
                        var p = pending[ev.data.id];
                        if (!p) return;
                        delete pending[ev.data.id];
                        if (ev.data.ok) p.resolve(ev.data.result); else p.reject(new Error(ev.data.error));
                    });
                    worker.onerror = function (ev) {
                        var err = new Error('Log parser stopped: ' + (ev.message || 'unknown error'));
                        Object.keys(pending).forEach(function (k) { pending[k].reject(err); });
                        pending = {};
                        try { worker.terminate(); } catch (e) { /* already gone */ }
                        worker = null;
                        if (ev.preventDefault) ev.preventDefault();
                    };
                }
                msg.id = ++reqId;
                return new Promise(function (resolve, reject) {
                    pending[msg.id] = { resolve: resolve, reject: reject };
                    worker.postMessage(msg, transfer || []);
                });
            }

            /* ---------- list ---------- */
            function setStatus(text, kind) {
                status.textContent = text || '';
                status.className = 'lv-status' + (kind ? ' lv-' + kind : '');
            }

            function renderList() {
                list.textContent = '';
                if (!state.logs.length) {
                    list.appendChild(h('p', { cls: 'lv-desc', text: 'No flight logs on this Solo yet. The Cube saves one each time it is armed.' }));
                    return;
                }
                var shown = state.showAll ? state.logs : state.logs.slice(0, LIST_SHOW);
                var table = h('div', { cls: 'lv-table' });
                shown.forEach(function (lg) {
                    var when = new Date(lg.mtime * 1000);
                    var name = h('span', { cls: 'lv-logname', text: 'Log ' + lg.n });
                    if (lg.latest) name.appendChild(h('span', { cls: 'lv-tag', text: 'newest' }));
                    if (lg.writing) name.appendChild(h('span', { cls: 'lv-tag lv-tag-warn', text: 'still being written' }));
                    var bView = h('button', { cls: 'lv-btn lv-btn-accent', type: 'button', text: 'View' });
                    var bDown = h('a', { cls: 'lv-btn', href: api.logUrl(lg.n), download: '', text: 'Download' });
                    if (state.armed) {
                        bView.disabled = true;
                        bDown.removeAttribute('href');
                        bDown.className += ' lv-disabled';
                    }
                    bView.addEventListener('click', guard(function () { openRemote(lg); }));
                    table.appendChild(h('div', { cls: 'lv-tr' }, [
                        name,
                        h('span', { cls: 'lv-dim', text: when.toLocaleString() }),
                        h('span', { cls: 'lv-dim', text: mb(lg.size) }),
                        h('span', { cls: 'lv-actions' }, [bView, bDown])
                    ]));
                });
                list.appendChild(table);
                if (!state.showAll && state.logs.length > LIST_SHOW) {
                    var more = h('button', { cls: 'lv-btn', type: 'button', text: 'Show all ' + state.logs.length + ' logs' });
                    more.addEventListener('click', guard(function () { state.showAll = true; renderList(); }));
                    list.appendChild(more);
                }
            }

            function refresh() {
                setStatus('Loading the list of flight logs…');
                api.listFlightLogs().then(guard(function (res) {
                    state.logs = res.logs || [];
                    state.armed = !!res.armed;
                    setStatus(state.armed ? 'The Solo is armed: flight logs open after landing (disarmed).' : '', state.armed ? 'warn' : '');
                    renderList();
                }))['catch'](function (e) { setStatus('Could not list the flight logs: ' + e.message, 'error'); });
            }

            /* ---------- open and show one log ---------- */
            function openBuffer(title, buffer) {
                progress.textContent = 'Reading the log…';
                return call({ cmd: 'parse', buffer: buffer }, [buffer]).then(guard(function (s) {
                    summary = s;
                    progress.textContent = '';
                    showSummary(title);
                }));
            }

            function startView(title) {
                state.current = title;
                summary = null;
                view.hidden = false;
                viewTitle.textContent = title;
                tiles.textContent = '';
                timeline.textContent = '';
                tabs.hidden = true;
                paneCharts.hidden = paneMessages.hidden = paneParams.hidden = true;
                chart.setData([]);
            }

            function openRemote(lg, confirmed) {
                startView('Log ' + lg.n);
                if (lg.size > BIG_LOG && !confirmed) {
                    progress.textContent = 'This log is ' + mb(lg.size) + '. Opening it on a phone can be slow. ';
                    var go = h('button', { cls: 'lv-btn', type: 'button', text: 'Open anyway' });
                    go.addEventListener('click', guard(function () { openRemote(lg, true); }));
                    progress.appendChild(go);
                    return;
                }
                progress.textContent = 'Downloading log ' + lg.n + '…';
                api.fetchFlightLog(lg.n, function (got, total) {
                    progress.textContent = 'Downloading log ' + lg.n + '… ' + mb(got) + (total ? ' of ' + mb(total) : '');
                }).then(function (buf) {
                    if (state.current !== 'Log ' + lg.n) return null;
                    return openBuffer('Log ' + lg.n, buf);
                })['catch'](function (e) { progress.textContent = 'Could not open log ' + lg.n + ': ' + e.message; });
            }

            function openLocal(file) {
                startView(file.name);
                progress.textContent = 'Reading ' + file.name + '…';
                file.arrayBuffer().then(function (buf) { return openBuffer(file.name, buf); })['catch'](function (e) {
                    progress.textContent = 'Could not open ' + file.name + ': ' + e.message;
                });
            }

            function tile(label, value, kind) {
                return h('div', { cls: 'lv-tile' + (kind ? ' lv-' + kind : '') }, [
                    h('span', { cls: 'lv-tlabel', text: label }), h('span', { cls: 'lv-tvalue', text: value })
                ]);
            }

            function showSummary(title) {
                var s = summary;
                viewTitle.textContent = title + (s.date ? ' · ' + new Date(s.date).toLocaleString() : '');
                var errs = s.messages.filter(function (m) { return m.sev === 'error'; }).length;
                var warns = s.messages.filter(function (m) { return m.sev === 'warning'; }).length;
                var vib = s.vibration.max, vibKind = vib === null ? '' : vib < 15 ? 'good' : vib < 30 ? 'warn' : 'error';
                var b = s.battery;
                tiles.textContent = '';
                [
                    tile('Firmware', s.version || '–'),
                    tile('Log length', dur(s.duration)),
                    tile('Flight time (armed)', s.armed.length ? dur(s.flightTime) : 'not armed'),
                    tile('Max height', num(s.maxAlt, 1, ' m')),
                    tile('Farthest from home', num(s.maxDist, 0, ' m')),
                    tile('Distance flown', num(s.path, 0, ' m')),
                    tile('Top speed', s.maxSpeed === null ? '–' : num(s.maxSpeed, 1, ' m/s') + ' (' + num(s.maxSpeed * 2.23694, 0, ' mph') + ')'),
                    tile('Battery', b.start === null ? '–' : num(b.start, 2, ' V') + ' → ' + num(b.min, 2, ' V')),
                    tile('Battery used', num(b.used, 0, ' mAh') + (b.maxCurrent === null ? '' : ' · max ' + num(b.maxCurrent, 1, ' A'))),
                    tile('GPS satellites', s.sats[0] === null ? '–' : s.sats[0] + '–' + s.sats[1] + (s.maxHdop === null ? '' : ' · HDOP ≤ ' + num(s.maxHdop, 2))),
                    tile('Vibration (max)', vib === null ? '–' : num(vib, 1, ' m/s²') + (s.vibration.clips ? ' · ' + s.vibration.clips + ' clips' : ''), s.vibration.clips ? 'error' : vibKind),
                    tile('Errors / warnings', errs + ' / ' + warns, errs ? 'error' : warns ? 'warn' : 'good')
                ].forEach(function (t) { tiles.appendChild(t); });
                if (s.skippedBytes > 1024) {
                    tiles.appendChild(tile('Damaged data skipped', mb(s.skippedBytes), 'warn'));
                }
                drawTimeline();
                // chart choices
                var have = {};
                s.types.forEach(function (t) { have[t.name] = t; });
                presetBox.textContent = '';
                var firstPreset = null;
                PRESETS.forEach(function (p) {
                    if (!have[p.type]) return;
                    var fields = p.fields.filter(function (f) { return have[p.type].fields.indexOf(f) >= 0; });
                    if (!fields.length) return;
                    var b2 = h('button', { cls: 'lv-chip', type: 'button', text: p.label });
                    b2.addEventListener('click', guard(function () { plotSeries(p.type, fields, b2); }));
                    presetBox.appendChild(b2);
                    if (!firstPreset) firstPreset = function () { plotSeries(p.type, fields, b2); };
                });
                typeSel.textContent = '';
                s.types.forEach(function (t) { typeSel.appendChild(h('option', { value: t.name, text: t.name + ' (' + t.count + ')' })); });
                fillFields();
                tabs.hidden = false;
                applyMessageFilter();
                applyParamFilter();
                showTab(state.tab);
                if (firstPreset) firstPreset();
            }

            function drawTimeline() {
                var s = summary, total = Math.max(s.duration, 0.001);
                timeline.textContent = '';
                if (!s.modes.length) return;
                var bar = h('div', { cls: 'lv-modebar' });
                var colors = {};
                s.modes.forEach(function (m, i) {
                    var end = i + 1 < s.modes.length ? s.modes[i + 1].t : total;
                    if (!(m.name in colors)) colors[m.name] = MODE_COLORS[Object.keys(colors).length % MODE_COLORS.length];
                    var seg = h('div', { cls: 'lv-seg', title: m.name + ' at ' + LV.parts.fmtTime(Math.max(0, m.t)) });
                    seg.style.left = (Math.max(0, m.t) / total * 100) + '%';
                    seg.style.width = (Math.max(0, end - Math.max(0, m.t)) / total * 100) + '%';
                    seg.style.background = colors[m.name];
                    bar.appendChild(seg);
                });
                var armedBar = h('div', { cls: 'lv-armedbar' });
                s.armed.forEach(function (r) {
                    var seg = h('div', { cls: 'lv-armseg' });
                    seg.style.left = (Math.max(0, r[0]) / total * 100) + '%';
                    seg.style.width = (Math.max(0, r[1] - r[0]) / total * 100) + '%';
                    armedBar.appendChild(seg);
                });
                var key = h('div', { cls: 'lv-modekey' });
                Object.keys(colors).forEach(function (name) {
                    var sw = h('span', { cls: 'lv-swatch' });
                    sw.style.background = colors[name];
                    key.appendChild(h('span', { cls: 'lv-keyitem' }, [sw, document.createTextNode(name)]));
                });
                key.appendChild(h('span', { cls: 'lv-keyitem' }, [h('span', { cls: 'lv-swatch lv-armswatch' }), document.createTextNode('Armed')]));
                timeline.appendChild(h('div', { cls: 'lv-tlabel', text: 'Flight modes' }));
                timeline.appendChild(bar);
                timeline.appendChild(armedBar);
                timeline.appendChild(key);
            }

            function fillFields() {
                fieldSel.textContent = '';
                var t = summary && summary.types.filter(function (x) { return x.name === typeSel.value; })[0];
                (t ? t.fields : []).forEach(function (f) { fieldSel.appendChild(h('option', { value: f, text: f })); });
            }

            function draw(series) {
                chart.setData(series, { modes: summary.modes, armed: summary.armed }, summary.duration);
            }

            function plotSeries(type, fields, chip) {
                Array.prototype.forEach.call(presetBox.children, function (c) { c.classList.toggle('lv-on', c === chip); });
                state.picked = fields.map(function (f) { return { type: type, field: f }; });
                legend.textContent = 'Loading…';
                return call({ cmd: 'series', type: type, fields: fields, maxPoints: 2000 }).then(guard(draw))['catch'](function (e) {
                    legend.textContent = e.message;
                });
            }

            function addPicked() {
                var type = typeSel.value, field = fieldSel.value;
                if (!type || !field) return;
                Array.prototype.forEach.call(presetBox.children, function (c) { c.classList.remove('lv-on'); });
                state.picked = state.picked.filter(function (p) { return !(p.type === type && p.field === field); });
                state.picked.push({ type: type, field: field });
                state.picked = state.picked.slice(-3);
                legend.textContent = 'Loading…';
                Promise.all(state.picked.map(function (p) {
                    return call({ cmd: 'series', type: p.type, fields: [p.field], maxPoints: 2000 });
                })).then(guard(function (parts) {
                    draw(parts.map(function (x) { return x[0]; }));
                }))['catch'](function (e) { legend.textContent = e.message; });
            }

            function sevRank(s) { return s === 'error' ? 2 : s === 'warning' ? 1 : 0; }

            function makeMatcher(text, hint) {
                if (!text) return function () { return true; };
                try {
                    var re = new RegExp(text, 'i');
                    if (hint) hint('');
                    return function (s) { return re.test(s); };
                } catch (e) {
                    var low = text.toLowerCase();
                    if (hint) hint(' (not a valid regular expression: matching as plain text)');
                    return function (s) { return s.toLowerCase().indexOf(low) >= 0; };
                }
            }

            function applyMessageFilter() {
                if (!summary) return;
                var min = state.sev === 'error' ? 2 : state.sev === 'warning' ? 1 : 0, note = '';
                var match = makeMatcher(state.filter, function (n) { note = n; });
                var items = summary.messages.filter(function (m) { return sevRank(m.sev) >= min && match(m.kind + ' ' + m.text); });
                msgCount.textContent = items.length + ' of ' + summary.messages.length + note;
                msgList.set(items);
            }

            function applyParamFilter() {
                if (!summary) return;
                var note = '', match = makeMatcher(state.pfilter, function (n) { note = n; });
                var items = summary.params.filter(function (p) { return match(p.name); });
                parCount.textContent = items.length + ' of ' + summary.params.length + note;
                parList.set(items);
            }

            function showTab(k) {
                state.tab = k;
                Object.keys(tabBtns).forEach(function (n) { tabBtns[n].classList.toggle('lv-on', n === k); });
                paneCharts.hidden = k !== 'charts';
                paneMessages.hidden = k !== 'messages';
                paneParams.hidden = k !== 'params';
                if (k === 'charts') chart.redraw();
                if (k === 'messages') msgList.paint();
                if (k === 'params') parList.paint();
            }

            /* ---------- events ---------- */
            btnRefresh.addEventListener('click', guard(refresh));
            fileInput.addEventListener('change', guard(function () {
                if (fileInput.files && fileInput.files[0]) openLocal(fileInput.files[0]);
                fileInput.value = '';
            }));
            btnClose.addEventListener('click', guard(function () { view.hidden = true; state.current = null; summary = null; }));
            typeSel.addEventListener('change', guard(fillFields));
            btnAdd.addEventListener('click', guard(addPicked));
            btnClear.addEventListener('click', guard(function () {
                state.picked = [];
                Array.prototype.forEach.call(presetBox.children, function (c) { c.classList.remove('lv-on'); });
                chart.setData([]);
            }));
            btnReset.addEventListener('click', guard(function () { chart.resetZoom(); }));
            sevSel.addEventListener('change', guard(function () { state.sev = sevSel.value; applyMessageFilter(); }));
            var t1 = null, t2 = null;
            msgFilter.addEventListener('input', guard(function () {
                clearTimeout(t1);
                t1 = setTimeout(guard(function () { state.filter = msgFilter.value; applyMessageFilter(); }), 200);
            }));
            parFilter.addEventListener('input', guard(function () {
                clearTimeout(t2);
                t2 = setTimeout(guard(function () { state.pfilter = parFilter.value; applyParamFilter(); }), 200);
            }));

            refresh();

            return {
                refresh: refresh,
                destroy: function () {
                    if (worker) { try { worker.terminate(); } catch (e) { /* gone */ } }
                    worker = null;
                    pending = {};
                    if (chart) chart.destroy();
                }
            };
        }
    };
})(window.SoloLogViewer);
