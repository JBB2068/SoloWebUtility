/* Solo Web Utility flight log viewer: small canvas line chart.
 * Up to 3 series on one time axis, flight-mode lines, armed time shaded, tap/hover readout,
 * drag across the chart to zoom in. Points arrive already thinned (<= ~2,000 per series). */
(function (LV) {
    'use strict';

    var COLORS = ['#38bdf8', '#f59e0b', '#10b981'];
    var M = { l: 52, r: 10, t: 16, b: 22 };

    function niceStep(range, count) {
        var raw = range / Math.max(1, count), mag = Math.pow(10, Math.floor(Math.log(raw) / Math.LN10));
        var n = raw / mag;
        return (n < 1.5 ? 1 : n < 3 ? 2 : n < 7 ? 5 : 10) * mag;
    }

    function fmtTime(s) {
        var total = Math.max(0, Math.round(s)), m = Math.floor(total / 60), r = total - m * 60;
        return m + ':' + (r < 10 ? '0' : '') + r;
    }

    function fmtVal(v) {
        if (!isFinite(v)) return '–';
        var a = Math.abs(v);
        return a >= 1000 ? v.toFixed(0) : a >= 100 ? v.toFixed(1) : a >= 1 ? v.toFixed(2) : v.toFixed(3);
    }

    function nearest(t, x) {
        var lo = 0, hi = t.length - 1;
        if (hi < 0) return -1;
        while (hi - lo > 1) {
            var mid = (lo + hi) >> 1;
            if (t[mid] < x) lo = mid; else hi = mid;
        }
        return Math.abs(t[lo] - x) <= Math.abs(t[hi] - x) ? lo : hi;
    }

    LV.parts.Chart = function (canvas, legend, guard, onZoom) {
        var ctx = canvas.getContext('2d'), data = [], extras = { modes: [], armed: [] };
        var full = [0, 1], view = [0, 1], cursor = null, drag = null, win = null;

        function size() {
            var dpr = window.devicePixelRatio || 1, w = canvas.clientWidth || 300, h = canvas.clientHeight || 240;
            if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
                canvas.width = Math.round(w * dpr);
                canvas.height = Math.round(h * dpr);
            }
            ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
            return { w: w, h: h };
        }

        function yRange() {
            var lo = Infinity, hi = -Infinity;
            data.forEach(function (s) {
                for (var k = 0; k < s.t.length; k++) {
                    if (s.t[k] < view[0] || s.t[k] > view[1]) continue;
                    var v = s.y[k];
                    if (isFinite(v)) { if (v < lo) lo = v; if (v > hi) hi = v; }
                }
            });
            if (lo === Infinity) return [0, 1];
            if (lo === hi) return [lo - 1, hi + 1];
            var pad = (hi - lo) * 0.06;
            return [lo - pad, hi + pad];
        }

        function draw() {
            var d = size(), W = d.w, H = d.h, pw = W - M.l - M.r, ph = H - M.t - M.b;
            ctx.clearRect(0, 0, W, H);
            if (!data.length) {
                ctx.fillStyle = '#64748b';
                ctx.font = '12px sans-serif';
                ctx.fillText('Pick something to plot.', M.l, M.t + 20);
                legend.textContent = '';
                return;
            }
            var yr = yRange();
            function X(t) { return M.l + (t - view[0]) / (view[1] - view[0]) * pw; }
            function Y(v) { return M.t + (1 - (v - yr[0]) / (yr[1] - yr[0])) * ph; }

            // armed time
            ctx.fillStyle = 'rgba(16,185,129,0.07)';
            extras.armed.forEach(function (r) {
                var a = Math.max(X(r[0]), M.l), b = Math.min(X(r[1]), M.l + pw);
                if (b > a) ctx.fillRect(a, M.t, b - a, ph);
            });
            // grid and labels
            ctx.strokeStyle = 'rgba(148,163,184,0.15)';
            ctx.fillStyle = '#94a3b8';
            ctx.font = '10px sans-serif';
            ctx.lineWidth = 1;
            var ys = niceStep(yr[1] - yr[0], 5);
            for (var v = Math.ceil(yr[0] / ys) * ys; v <= yr[1]; v += ys) {
                var y = Math.round(Y(v)) + 0.5;
                ctx.beginPath(); ctx.moveTo(M.l, y); ctx.lineTo(M.l + pw, y); ctx.stroke();
                ctx.fillText(fmtVal(Math.abs(v) < ys / 1e6 ? 0 : v), 4, y + 3);
            }
            var xs = niceStep(view[1] - view[0], Math.max(2, Math.floor(pw / 70)));
            for (var t = Math.ceil(view[0] / xs) * xs; t <= view[1]; t += xs) {
                var x = Math.round(X(t)) + 0.5;
                ctx.beginPath(); ctx.moveTo(x, M.t); ctx.lineTo(x, M.t + ph); ctx.stroke();
                ctx.fillText(fmtTime(t), x - 12, H - 6);
            }
            // flight modes
            ctx.save();
            ctx.setLineDash([3, 3]);
            ctx.strokeStyle = 'rgba(245,158,11,0.55)';
            ctx.fillStyle = '#f59e0b';
            extras.modes.forEach(function (m) {
                if (m.t < view[0] || m.t > view[1]) return;
                var x = Math.round(X(m.t)) + 0.5;
                ctx.beginPath(); ctx.moveTo(x, M.t); ctx.lineTo(x, M.t + ph); ctx.stroke();
                ctx.fillText(m.name, x + 3, M.t - 4);
            });
            ctx.restore();
            // series
            ctx.save();
            ctx.beginPath(); ctx.rect(M.l, M.t, pw, ph); ctx.clip();
            data.forEach(function (s, i) {
                ctx.strokeStyle = COLORS[i % COLORS.length];
                ctx.lineWidth = 1.4;
                ctx.beginPath();
                var pen = false;
                for (var k = 0; k < s.t.length; k++) {
                    var vv = s.y[k];
                    if (!isFinite(vv)) { pen = false; continue; }
                    var px = X(s.t[k]), py = Y(vv);
                    if (pen) ctx.lineTo(px, py); else { ctx.moveTo(px, py); pen = true; }
                }
                ctx.stroke();
            });
            ctx.restore();
            // zoom selection
            if (drag && drag.moved) {
                ctx.fillStyle = 'rgba(56,189,248,0.15)';
                ctx.fillRect(Math.min(drag.x0, drag.x1), M.t, Math.abs(drag.x1 - drag.x0), ph);
            }
            // readout
            var parts = [];
            if (cursor !== null && cursor >= view[0] && cursor <= view[1]) {
                var cx = Math.round(X(cursor)) + 0.5;
                ctx.strokeStyle = 'rgba(241,245,249,0.5)';
                ctx.beginPath(); ctx.moveTo(cx, M.t); ctx.lineTo(cx, M.t + ph); ctx.stroke();
                parts.push(fmtTime(cursor));
            }
            data.forEach(function (s, i) {
                var txt = s.name;
                if (cursor !== null) {
                    var k = nearest(s.t, cursor);
                    if (k >= 0 && isFinite(s.y[k])) {
                        txt += ' ' + fmtVal(s.y[k]);
                        ctx.fillStyle = COLORS[i % COLORS.length];
                        ctx.beginPath(); ctx.arc(X(s.t[k]), Y(s.y[k]), 3, 0, 6.3); ctx.fill();
                    }
                }
                parts.push('<span style="color:' + COLORS[i % COLORS.length] + '">● ' + txt.replace(/[<>&]/g, '') + '</span>');
            });
            legend.innerHTML = parts.join('&nbsp;&nbsp;');
        }

        function tAt(ev) {
            var r = canvas.getBoundingClientRect(), pw = r.width - M.l - M.r;
            var x = Math.min(Math.max(ev.clientX - r.left, M.l), M.l + pw);
            return { x: x, t: view[0] + (x - M.l) / pw * (view[1] - view[0]) };
        }

        canvas.addEventListener('pointerdown', guard(function (ev) {
            var p = tAt(ev);
            drag = { x0: p.x, x1: p.x, t0: p.t, t1: p.t, moved: false };
            cursor = p.t;
            try { canvas.setPointerCapture(ev.pointerId); } catch (e) { /* older browsers */ }
            draw();
        }));
        canvas.addEventListener('pointermove', guard(function (ev) {
            var p = tAt(ev);
            cursor = p.t;
            if (drag) {
                drag.x1 = p.x; drag.t1 = p.t;
                if (Math.abs(drag.x1 - drag.x0) > 6) drag.moved = true;
            }
            draw();
        }));
        canvas.addEventListener('pointerup', guard(function () {
            if (drag && drag.moved) {
                var a = Math.min(drag.t0, drag.t1), b = Math.max(drag.t0, drag.t1);
                if (b - a > 0.05) { view = [a, b]; if (onZoom) onZoom(true); }
            }
            drag = null;
            draw();
        }));
        canvas.addEventListener('pointerleave', guard(function () {
            if (!drag) { cursor = null; draw(); }
        }));

        var ro = null;
        if (window.ResizeObserver) {
            ro = new ResizeObserver(guard(function () { draw(); }));
            ro.observe(canvas);
        }

        return {
            setData: function (series, ex, duration) {
                data = series || [];
                extras = ex || { modes: [], armed: [] };
                var lo = Infinity, hi = -Infinity;
                data.forEach(function (s) { if (s.t.length) { lo = Math.min(lo, s.t[0]); hi = Math.max(hi, s.t[s.t.length - 1]); } });
                if (lo === Infinity) { lo = 0; hi = duration || 1; }
                if (hi <= lo) hi = lo + 1;
                full = [lo, hi];
                view = full.slice();
                if (win) {                       // a preferred window (e.g. the flights in a long telemetry log)
                    var a = Math.max(lo, win[0]), b = Math.min(hi, win[1]);
                    if (b > a) view = [a, b];
                }
                cursor = null;
                if (onZoom) onZoom(view[0] > full[0] || view[1] < full[1]);
                draw();
            },
            /* Open at this time range from now on (null = the whole log). Reset zoom still shows everything. */
            setWindow: function (a, b) { win = (b > a) ? [a, b] : null; },
            zoomTo: function (a, b) {
                if (!(b > a)) return;
                var x = Math.max(full[0], a), y = Math.min(full[1], b);
                if (y > x) { view = [x, y]; if (onZoom) onZoom(true); draw(); }
            },
            resetZoom: function () { view = full.slice(); if (onZoom) onZoom(false); draw(); },
            redraw: draw,
            destroy: function () { if (ro) ro.disconnect(); }
        };
    };

    LV.parts.fmtTime = fmtTime;
    LV.parts.fmtVal = fmtVal;
})(window.SoloLogViewer);
