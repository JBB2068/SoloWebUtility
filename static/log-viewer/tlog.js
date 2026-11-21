/* Solo Web Utility flight log viewer: MAVLink telemetry log (.tlog) reader.
 *
 * The controller's "tlog" service records the telemetry stream it gets from the Solo in
 * /log/solo.tlog (rolled over into solo.tlog.1, .2, ...). Each record is an 8-byte big-endian
 * timestamp (microseconds) followed by one MAVLink v1 (0xFE) or v2 (0xFD) frame. One file can hold
 * several flights. Loaded by log-worker.js inside a Web Worker, and by tools/test_logviewer.js.
 *
 * Files can be 100 MB, so the reader works in a stream: log-worker.js feeds it the download chunk by
 * chunk (begin / feed / finish) and it keeps only what the viewer shows: the Cube's heartbeats and
 * text messages, the chartable messages at most five times a second, and the latest value of each
 * parameter. Memory stays small whatever the file size.
 *
 * Fields are read by their position in the MAVLink wire format (fields sorted by size), and
 * MAVLink 2 frames may be shorter than the full message (trailing zeros are cut), so a field past
 * the end of a payload reads as 0.
 *
 * API: LVTlog.looksLike(u8), .begin(), .feed(u8), .finish() -> summary, .parse(buffer) -> summary,
 *      .series(type, fields, maxPoints)
 */
var LVTlog = (function () {
'use strict';

var COPTER_MODES = { 0: 'Stabilize', 1: 'Acro', 2: 'Alt Hold', 3: 'Auto', 4: 'Guided', 5: 'Loiter',
    6: 'RTL', 7: 'Circle', 9: 'Land', 11: 'Drift', 13: 'Sport', 14: 'Flip', 15: 'AutoTune',
    16: 'PosHold', 17: 'Brake', 18: 'Throw', 19: 'Avoid ADSB', 20: 'Guided NoGPS', 21: 'Smart RTL',
    22: 'FlowHold', 23: 'Follow', 24: 'ZigZag', 25: 'SystemID', 26: 'Autorotate', 27: 'Auto RTL',
    28: 'Turtle' };

/* Chartable messages: name -> {id, fields: {Label: [type, offset, scale]}} */
var DEFS = {
    ATTITUDE: { id: 30, fields: { Roll: ['f', 4, 57.29578], Pitch: ['f', 8, 57.29578], Yaw: ['f', 12, 57.29578],
        RollRate: ['f', 16, 57.29578], PitchRate: ['f', 20, 57.29578], YawRate: ['f', 24, 57.29578] } },
    GLOBAL_POSITION_INT: { id: 33, fields: { RelAlt: ['i', 16, 0.001], Alt: ['i', 12, 0.001], Lat: ['i', 4, 1e-7],
        Lng: ['i', 8, 1e-7], VX: ['h', 20, 0.01], VY: ['h', 22, 0.01], VZ: ['h', 24, 0.01], Heading: ['H', 26, 0.01] } },
    VFR_HUD: { id: 74, fields: { GroundSpeed: ['f', 4, 1], AirSpeed: ['f', 0, 1], Alt: ['f', 8, 1], Climb: ['f', 12, 1],
        Heading: ['h', 16, 1], Throttle: ['H', 18, 1] } },
    SYS_STATUS: { id: 1, fields: { Volt: ['H', 14, 0.001], Curr: ['h', 16, 0.01], Remaining: ['b', 30, 1],
        Load: ['H', 12, 0.1], DropRate: ['H', 18, 0.01] } },
    BATTERY_STATUS: { id: 147, fields: { CurrTot: ['i', 0, 1], Curr: ['h', 30, 0.01], Remaining: ['b', 35, 1],
        Temp: ['h', 8, 0.01] } },
    GPS_RAW_INT: { id: 24, fields: { NSats: ['B', 29, 1], Fix: ['B', 28, 1], HDop: ['H', 20, 0.01], Spd: ['H', 24, 0.01],
        Alt: ['i', 16, 0.001] } },
    VIBRATION: { id: 241, fields: { VibeX: ['f', 8, 1], VibeY: ['f', 12, 1], VibeZ: ['f', 16, 1],
        Clip0: ['I', 20, 1], Clip1: ['I', 24, 1], Clip2: ['I', 28, 1] } },
    EKF_STATUS_REPORT: { id: 193, fields: { VelVar: ['f', 0, 1], PosHorizVar: ['f', 4, 1], PosVertVar: ['f', 8, 1],
        CompassVar: ['f', 12, 1] } },
    RC_CHANNELS: { id: 65, fields: { Ch1: ['H', 4, 1], Ch2: ['H', 6, 1], Ch3: ['H', 8, 1], Ch4: ['H', 10, 1],
        Ch5: ['H', 12, 1], Ch6: ['H', 14, 1], Ch7: ['H', 16, 1], Ch8: ['H', 18, 1], RSSI: ['B', 41, 1] } },
    SERVO_OUTPUT_RAW: { id: 36, fields: { Motor1: ['H', 4, 1], Motor2: ['H', 6, 1], Motor3: ['H', 8, 1],
        Motor4: ['H', 10, 1], Servo5: ['H', 12, 1], Servo6: ['H', 14, 1], Servo7: ['H', 16, 1], Servo8: ['H', 18, 1] } }
};
var SIZES = { b: 1, B: 1, h: 2, H: 2, i: 4, I: 4, f: 4 };
var HEARTBEAT = 0, PARAM_VALUE = 22, STATUSTEXT = 253;
var GAP_SECONDS = 5;
var MIN_FLIGHT = 3;                   // arm periods shorter than this are not listed as flights

var log = null;   // {u8, dv, recs: {msgid: {off: Uint32Array, len: Uint8Array, t: Float64Array, src: Uint16Array, n}}, t0, t1}

function readTs(dv, i) {
    return dv.getUint32(i, false) * 4294967296 + dv.getUint32(i + 4, false);
}

function frameAt(u8, i, n) {
    /* -> {len (whole frame), sys, comp, msgid, pay (payload offset), plen} or null */
    if (i + 8 + 8 > n) return null;
    var m = u8[i + 8], plen = u8[i + 9];
    if (m === 0xFE) {
        return { len: 6 + plen + 2, sys: u8[i + 11], comp: u8[i + 12], msgid: u8[i + 13], pay: i + 14, plen: plen };
    }
    if (m === 0xFD) {
        if (i + 18 > n) return null;
        var signed = (u8[i + 10] & 1) ? 13 : 0;
        return { len: 10 + plen + 2 + signed, sys: u8[i + 13], comp: u8[i + 14],
                 msgid: u8[i + 15] | (u8[i + 16] << 8) | (u8[i + 17] << 16), pay: i + 18, plen: plen };
    }
    return null;
}

function looksLike(u8) {
    var n = u8.length, i = 0, good = 0;
    for (var k = 0; k < 4; k++) {
        var f = frameAt(u8, i, n);
        if (!f) break;
        good++;
        i += 8 + f.len;
        if (i >= n) return good > 0;
    }
    return good >= 3;
}

var VEHICLE = (1 << 8) | 1;          // sysid 1, compid 1 (the Cube)
var KEEP = {};                        // message ids kept for the viewer
Object.keys(DEFS).forEach(function (k) { KEEP[DEFS[k].id] = true; });
KEEP[HEARTBEAT] = true;
var MIN_GAP = 0.2;                    // seconds between kept records of one chartable message
var GAP_SAMPLE = 0.5;                 // seconds between telemetry-gap samples

function readAt(dv, base, plen, t, pos) {
    if (pos + SIZES[t] > plen) return 0;           // cut by MAVLink 2 truncation
    var p = base + pos;
    switch (t) {
        case 'b': return dv.getInt8(p);
        case 'B': return dv.getUint8(p);
        case 'h': return dv.getInt16(p, true);
        case 'H': return dv.getUint16(p, true);
        case 'i': return dv.getInt32(p, true);
        case 'I': return dv.getUint32(p, true);
        case 'f': return dv.getFloat32(p, true);
    }
    return 0;
}

function textAt(u8, base, plen, pos, max) {
    var s = '';
    for (var k = 0; k < max && pos + k < plen; k++) {
        var c = u8[base + pos + k];
        if (c === 0) break;
        s += String.fromCharCode(c);
    }
    return s.replace(/\s+$/, '');
}

/* Streaming first pass. */
function Builder() {
    this.store = new Uint8Array(1 << 20);   // payloads of the kept messages
    this.used = 0;
    this.recs = {};                         // msgid -> {off, len, t, src, n}
    this.params = {};
    this.gaps = [];                         // sampled times of any telemetry
    this.lastGap = -1e18;
    this.lastKept = {};
    this.carry = null;
    this.bad = 0; this.size = 0; this.frames = 0;
    this.t0 = null; this.t1 = 0;
}

Builder.prototype.keep = function (id, src, ts, u8, f) {
    var L = this.recs[id];
    if (!L) L = this.recs[id] = { off: new Uint32Array(256), len: new Uint8Array(256), t: new Float64Array(256), src: new Uint16Array(256), n: 0 };
    if (L.n === L.off.length) {
        var cap = L.n * 2, o2 = new Uint32Array(cap), l2 = new Uint8Array(cap), t2 = new Float64Array(cap), s2 = new Uint16Array(cap);
        o2.set(L.off); l2.set(L.len); t2.set(L.t); s2.set(L.src);
        L.off = o2; L.len = l2; L.t = t2; L.src = s2;
    }
    if (this.used + f.plen > this.store.length) {
        var bigger = new Uint8Array(Math.max(this.store.length * 2, this.used + f.plen));
        bigger.set(this.store.subarray(0, this.used));
        this.store = bigger;
    }
    this.store.set(u8.subarray(f.pay, f.pay + f.plen), this.used);
    L.off[L.n] = this.used; L.len[L.n] = f.plen; L.t[L.n] = ts; L.src[L.n] = src;
    L.n++;
    this.used += f.plen;
};

Builder.prototype.run = function (u8, final) {
    var n = u8.length, i = 0, dv = new DataView(u8.buffer, u8.byteOffset, u8.length);
    while (i + 16 <= n) {
        var f = frameAt(u8, i, n);
        if (!f) {
            if (!final && u8[i + 8] === 0xFD && i + 18 > n) break;     // header not complete yet
            i++; this.bad++; continue;
        }
        var next = i + 8 + f.len;
        if (next > n) { if (final) { i++; this.bad++; continue; } break; }   // frame not complete yet
        if (next + 9 > n) {
            if (!final) break;                                          // cannot see the next record yet
        } else if (u8[next + 8] !== 0xFE && u8[next + 8] !== 0xFD) {
            i++; this.bad++; continue;
        }
        var ts = (dv.getUint32(i, false) * 4294967296 + dv.getUint32(i + 4, false)) / 1e6;
        if (this.t0 === null) this.t0 = ts;
        if (ts > this.t1) this.t1 = ts;
        this.frames++;
        var src = (f.sys << 8) | f.comp, id = f.msgid;
        if (src === VEHICLE && ts - this.lastGap >= GAP_SAMPLE) { this.gaps.push(ts); this.lastGap = ts; }
        if (id === PARAM_VALUE) {
            if (src === VEHICLE) {
                var pname = textAt(u8, f.pay, f.plen, 8, 16);
                if (pname) this.params[pname] = readAt(dv, f.pay, f.plen, 'f', 0);
            }
        } else if (id === STATUSTEXT) {
            this.keep(id, src, ts, u8, f);
        } else if (src === VEHICLE && KEEP[id]) {
            if (id === HEARTBEAT || !(ts - (this.lastKept[id] === undefined ? -1e18 : this.lastKept[id]) < MIN_GAP)) {
                this.lastKept[id] = ts;
                this.keep(id, src, ts, u8, f);
            }
        }
        i = next;
    }
    this.carry = i < n ? u8.slice(i) : null;
};

Builder.prototype.feed = function (chunk) {
    this.size += chunk.length;
    var u8 = chunk;
    if (this.carry && this.carry.length) {
        u8 = new Uint8Array(this.carry.length + chunk.length);
        u8.set(this.carry, 0);
        u8.set(chunk, this.carry.length);
    }
    this.run(u8, false);
};

Builder.prototype.end = function () {
    if (this.carry && this.carry.length) { var rest = this.carry; this.carry = null; this.run(rest, true); }
    var u8 = this.store.subarray(0, this.used);
    return { u8: u8, dv: new DataView(u8.buffer, u8.byteOffset, u8.length), recs: this.recs, params: this.params,
             gaps: this.gaps, bad: this.bad, frames: this.frames, t0: this.t0 === null ? 0 : this.t0, t1: this.t1,
             size: this.size };
};

function rd(off, plen, t, pos) { return readAt(log.dv, off, plen, t, pos); }

function text(off, plen, pos, max) { return textAt(log.u8, off, plen, pos, max); }

function each(id, fn, anySource) {
    var L = log.recs[id];
    if (!L) return;
    for (var k = 0; k < L.n; k++) {
        if (!anySource && L.src[k] !== VEHICLE) continue;
        fn(L.off[k], L.len[k], L.t[k] - log.t0, L.src[k]);
    }
}

function haversine(lat1, lon1, lat2, lon2) {
    var R = 6371000, rad = Math.PI / 180;
    var dLat = (lat2 - lat1) * rad, dLon = (lon2 - lon1) * rad;
    var a = Math.sin(dLat / 2) * Math.sin(dLat / 2) +
            Math.cos(lat1 * rad) * Math.cos(lat2 * rad) * Math.sin(dLon / 2) * Math.sin(dLon / 2);
    return 2 * R * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

function summarize() {
    var duration = Math.max(0, log.t1 - log.t0);
    var messages = [], modes = [], armed = [], params = {}, version = null;

    // heartbeats: arming and flight modes
    var armedAt = null, lastMode = null, lastHb = null;
    each(HEARTBEAT, function (off, plen, t) {
        var custom = rd(off, plen, 'I', 0), base = rd(off, plen, 'B', 6);
        var isArmed = (base & 0x80) !== 0;
        if (isArmed && armedAt === null) { armedAt = t; messages.push({ t: t, kind: 'Event', sev: 'info', text: 'Armed' }); }
        if (!isArmed && armedAt !== null) { armed.push([armedAt, t]); armedAt = null; messages.push({ t: t, kind: 'Event', sev: 'info', text: 'Disarmed' }); }
        if (custom !== lastMode) {
            var name = COPTER_MODES[custom] || ('Mode ' + custom);
            modes.push({ t: t, num: custom, name: name });
            messages.push({ t: t, kind: 'Mode', sev: 'info', text: 'Flight mode: ' + name });
            lastMode = custom;
        }
        lastHb = t;
    });
    if (armedAt !== null) {
        armed.push([armedAt, lastHb !== null ? lastHb : duration]);
        messages.push({ t: lastHb !== null ? lastHb : duration, kind: 'Event', sev: 'warning', text: 'Telemetry ended while armed' });
    }

    // the Cube's (and gimbal's) text messages, with their own severity
    each(STATUSTEXT, function (off, plen, t, src) {
        var sevNum = rd(off, plen, 'B', 0), txt = text(off, plen, 1, 50);
        if (!txt) return;
        if (!version && /(ArduCopter|APM:Copter)\s*V?\d/i.test(txt)) version = txt;
        messages.push({ t: t, kind: (src & 0xff) === 1 ? 'Message' : 'Message ' + (src & 0xff),
                        sev: sevNum <= 3 ? 'error' : sevNum === 4 ? 'warning' : 'info', text: txt });
    }, true);

    // gaps in the telemetry (link lost, Solo switched off)
    var all = log.gaps.slice().sort(function (a, b) { return a - b; });
    for (var g = 1; g < all.length; g++) {
        var gap = all[g] - all[g - 1];
        if (gap > GAP_SECONDS) {
            var at = all[g - 1] - log.t0, inFlight = armed.some(function (r) { return at >= r[0] && at < r[1]; });
            messages.push({ t: at, kind: 'Link', sev: inFlight ? 'warning' : 'info',
                            text: 'No telemetry for ' + Math.round(gap) + ' s' + (inFlight ? ' while armed' : '') });
        }
    }

    Object.keys(log.params).forEach(function (k) { params[k] = log.params[k]; });

    function flightOf(t) {
        for (var k = 0; k < armed.length; k++) if (t >= armed[k][0] && t < armed[k][1]) return k;
        return -1;
    }

    var maxAlt = null, maxDist = null, path = 0, homes = {}, lastPos = {}, maxSpd = null;
    each(33, function (off, plen, t) {
        var f = flightOf(t);
        if (f < 0) return;
        var lat = rd(off, plen, 'i', 4) / 1e7, lng = rd(off, plen, 'i', 8) / 1e7, rel = rd(off, plen, 'i', 16) / 1000;
        maxAlt = maxAlt === null ? rel : Math.max(maxAlt, rel);
        if (!lat && !lng) return;
        if (!homes[f]) homes[f] = [lat, lng];
        var d = haversine(homes[f][0], homes[f][1], lat, lng);
        maxDist = maxDist === null ? d : Math.max(maxDist, d);
        if (lastPos[f]) { var step = haversine(lastPos[f][0], lastPos[f][1], lat, lng); if (step < 100) path += step; }
        lastPos[f] = [lat, lng];
    });
    each(74, function (off, plen, t) {
        if (flightOf(t) < 0) return;
        var s = rd(off, plen, 'f', 4);
        maxSpd = maxSpd === null ? s : Math.max(maxSpd, s);
    });
    var vStart = null, vMin = null, cMax = null;
    each(1, function (off, plen, t) {
        if (flightOf(t) < 0) return;
        var v = rd(off, plen, 'H', 14) / 1000, c = rd(off, plen, 'h', 16);
        if (v > 0) { if (vStart === null) vStart = v; vMin = vMin === null ? v : Math.min(vMin, v); }
        if (c >= 0) { c = c / 100; cMax = cMax === null ? c : Math.max(cMax, c); }
    });
    var used = null, firstMah = {}, lastMah = {};
    each(147, function (off, plen, t) {
        var f = flightOf(t);
        if (f < 0) return;
        var mah = rd(off, plen, 'i', 0);
        if (mah < 0) return;
        if (!(f in firstMah)) firstMah[f] = mah;
        lastMah[f] = mah;
    });
    Object.keys(firstMah).forEach(function (f) { used = (used || 0) + (lastMah[f] - firstMah[f]); });
    var sMin = null, sMax = null, hdop = null;
    each(24, function (off, plen, t) {
        if (flightOf(t) < 0) return;
        var s = rd(off, plen, 'B', 29), e = rd(off, plen, 'H', 20);
        sMin = sMin === null ? s : Math.min(sMin, s);
        sMax = sMax === null ? s : Math.max(sMax, s);
        if (e && e !== 65535) hdop = hdop === null ? e / 100 : Math.max(hdop, e / 100);
    });
    var vibe = null, clips = 0;
    each(241, function (off, plen, t) {
        if (flightOf(t) < 0) return;
        [8, 12, 16].forEach(function (p) { var v = rd(off, plen, 'f', p); vibe = vibe === null ? v : Math.max(vibe, v); });
        var c = rd(off, plen, 'I', 20) + rd(off, plen, 'I', 24) + rd(off, plen, 'I', 28);
        if (c > clips) clips = c;
    });

    messages.sort(function (a, b) { return a.t - b.t; });
    var flightTime = 0;
    armed.forEach(function (r) { flightTime += r[1] - r[0]; });

    var types = [];
    Object.keys(DEFS).forEach(function (name) {
        var L = log.recs[DEFS[name].id];
        var count = 0;
        if (L) for (var k = 0; k < L.n; k++) if (L.src[k] === VEHICLE) count++;
        if (count) types.push({ name: name, count: count, fields: Object.keys(DEFS[name].fields) });
    });

    return {
        kind: 'tlog', size: log.size, skippedBytes: log.bad, frames: log.frames, duration: duration, version: version,
        date: log.t0 > 1.5e9 ? new Date(log.t0 * 1000).toISOString() : null,
        flightTime: flightTime, armed: armed, flights: armed.filter(function (r) { return r[1] - r[0] >= MIN_FLIGHT; }).map(function (r) { return [r[0], r[1]]; }),
        modes: modes, messages: messages,
        params: Object.keys(params).sort().map(function (k) { return { name: k, value: params[k] }; }),
        maxAlt: maxAlt, maxDist: maxDist, path: maxDist === null ? null : path, maxSpeed: maxSpd,
        sats: [sMin, sMax], maxHdop: hdop,
        battery: { start: vStart, min: vMin, maxCurrent: cMax, used: used },
        vibration: { max: vibe, clips: clips },
        types: types, t0: log.t0
    };
}

function series(type, fields, maxPoints) {
    var def = DEFS[type];
    if (!def || !log.recs[def.id]) throw new Error('No ' + type + ' messages in this log');
    var ts = [], ys = fields.map(function () { return []; });
    each(def.id, function (off, plen, t) {
        ts.push(t);
        for (var j = 0; j < fields.length; j++) {
            var fd = def.fields[fields[j]];
            ys[j].push(fd ? rd(off, plen, fd[0], fd[1]) * fd[2] : NaN);
        }
    });
    var n = ts.length, out = [];
    for (var j2 = 0; j2 < fields.length; j2++) {
        var t = [], y = [];
        if (n <= maxPoints) { t = ts; y = ys[j2]; }
        else {
            var buckets = Math.floor(maxPoints / 2), size = n / buckets;
            for (var b = 0; b < buckets; b++) {
                var s = Math.floor(b * size), e = Math.min(n, Math.floor((b + 1) * size));
                var lo = Infinity, hi = -Infinity, ilo = s, ihi = s;
                for (var q = s; q < e; q++) {
                    var v = ys[j2][q];
                    if (v < lo) { lo = v; ilo = q; }
                    if (v > hi) { hi = v; ihi = q; }
                }
                if (lo === Infinity) continue;
                if (ilo <= ihi) { t.push(ts[ilo], ts[ihi]); y.push(lo, hi); }
                else { t.push(ts[ihi], ts[ilo]); y.push(hi, lo); }
            }
        }
        out.push({ name: type + '.' + fields[j2], t: Float64Array.from(t), y: Float64Array.from(y) });
    }
    return out;
}

function finish(builder) {
    log = builder.end();
    if (!log.frames) { log = null; throw new Error('No MAVLink telemetry found in this file.'); }
    log.summary = summarize();
    return log.summary;
}

var building = null;

return {
    looksLike: looksLike,
    begin: function () { building = new Builder(); log = null; },
    feed: function (u8) { if (!building) throw new Error('begin() first'); building.feed(u8); },
    finish: function () { var b = building; building = null; return finish(b); },
    parse: function (buffer) {
        var b = new Builder();
        b.feed(new Uint8Array(buffer));
        return finish(b);
    },
    series: function (type, fields, maxPoints) {
        if (!log) throw new Error('No log loaded');
        return series(type, fields, maxPoints);
    },
    DEFS: DEFS
};
})();

if (typeof module !== 'undefined' && module.exports) module.exports = LVTlog;
