/* Solo Web Utility flight log viewer: the one entry point.
 *
 * Self-contained module in static/log-viewer/. The utility's page only has
 *     <div id="logViewerMount"></div>
 *     <script src="/log-viewer/log-viewer.js?v=..." defer></script>
 * and this file does the rest:
 *   - mounts into that element's Shadow DOM, so the viewer's styles stay inside it and the
 *     page's styles cannot change the viewer;
 *   - loads its other files (api.js, chart.js, flight-panel.js) and starts only when the
 *     Parameters tab is actually on screen;
 *   - runs every handler and timer through guard(): an exception replaces only the viewer's
 *     panel with an error and a Reload button; the rest of the utility is untouched;
 *   - keeps all its state inside the mounted instance (window.SoloLogViewer holds only code).
 * Public API: SoloLogViewer.mount(element) -> {unmount()}, SoloLogViewer.unmount(element).
 */
(function () {
    'use strict';
    if (window.SoloLogViewer && window.SoloLogViewer.mount) return;

    var script = document.currentScript;
    var src = script ? script.src : '/log-viewer/log-viewer.js';
    var base = src.replace(/[?#].*$/, '').replace(/[^\/]*$/, '');
    var vm = /[?&]v=([^&#]+)/.exec(src);
    var q = vm ? '?v=' + vm[1] : '';
    var PARTS = ['api.js', 'chart.js', 'flight-panel.js'];

    var LV = window.SoloLogViewer = { parts: {}, base: base, q: q };
    var loading = null;
    var instances = [];

    function loadParts() {
        if (loading) return loading;
        loading = PARTS.reduce(function (p, name) {
            return p.then(function () {
                return new Promise(function (resolve, reject) {
                    var s = document.createElement('script');
                    s.src = base + name + q;
                    s.onload = resolve;
                    s.onerror = function () { reject(new Error('could not load ' + name)); };
                    document.head.appendChild(s);
                });
            });
        }, Promise.resolve());
        loading['catch'](function () { loading = null; });
        return loading;
    }

    LV.mount = function (host) {
        if (!host) return null;
        for (var k = 0; k < instances.length; k++) if (instances[k].host === host) return instances[k];

        var root = host.attachShadow ? (host.shadowRoot || host.attachShadow({ mode: 'open' })) : host;
        var inst = { host: host, panel: null, started: false, observer: null };
        var body = null, failed = false;

        function render() {
            root.innerHTML = '';
            var css = document.createElement('link');
            css.rel = 'stylesheet';
            css.href = base + 'log-viewer.css' + q;
            root.appendChild(css);
            body = document.createElement('div');
            body.className = 'lv';
            root.appendChild(body);
        }

        function fail(err) {
            if (failed) return;
            failed = true;
            try { if (inst.panel) inst.panel.destroy(); } catch (e) { /* already broken */ }
            inst.panel = null;
            try { console.error('[log viewer]', err); } catch (e) { /* no console */ }
            render();
            var box = document.createElement('div');
            box.className = 'lv-card lv-crash';
            var p = document.createElement('p');
            p.textContent = 'The flight log viewer hit an error and stopped: ' + (err && err.message || err) +
                '. The rest of the utility is not affected.';
            var btn = document.createElement('button');
            btn.className = 'lv-btn';
            btn.type = 'button';
            btn.textContent = 'Reload viewer';
            btn.addEventListener('click', function () { failed = false; start(); });
            box.appendChild(p);
            box.appendChild(btn);
            body.appendChild(box);
        }

        function guard(fn) {
            return function () {
                if (failed) return undefined;
                try { return fn.apply(this, arguments); } catch (e) { fail(e); return undefined; }
            };
        }

        function start() {
            render();
            var wait = document.createElement('p');
            wait.className = 'lv-desc';
            wait.textContent = 'Loading the flight log viewer…';
            body.appendChild(wait);
            loadParts().then(guard(function () {
                body.removeChild(wait);
                inst.panel = LV.parts.FlightPanel.create(body, { guard: guard, fail: fail, base: base, q: q });
            }))['catch'](fail);
        }

        function startWhenVisible() {
            if (inst.started) return;
            inst.started = true;
            if (inst.observer) { inst.observer.disconnect(); inst.observer = null; }
            start();
        }

        render();
        if (window.IntersectionObserver) {
            inst.observer = new IntersectionObserver(function (entries) {
                entries.forEach(function (e) { if (e.isIntersecting) { try { startWhenVisible(); } catch (err) { fail(err); } } });
            });
            inst.observer.observe(host);
        } else {
            startWhenVisible();
        }

        inst.unmount = function () {
            try { if (inst.observer) inst.observer.disconnect(); } catch (e) { /* ignore */ }
            try { if (inst.panel) inst.panel.destroy(); } catch (e) { /* ignore */ }
            root.innerHTML = '';
            instances = instances.filter(function (x) { return x !== inst; });
        };
        instances.push(inst);
        return inst;
    };

    LV.unmount = function (host) {
        instances.filter(function (x) { return x.host === host; }).forEach(function (x) { x.unmount(); });
    };

    function auto() {
        try {
            var el = document.getElementById('logViewerMount');
            if (el) LV.mount(el);
        } catch (e) {
            try { console.error('[log viewer] not started:', e); } catch (e2) { /* no console */ }
        }
    }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', auto);
    else auto();
})();
