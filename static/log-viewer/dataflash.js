/* Solo Web Utility flight log viewer: ArduPilot DataFlash (.BIN) reader.
 *
 * Loaded by log-worker.js inside a Web Worker (never on the page's main thread), and by
 * tools/test_logviewer.js under node. The format is self-describing: every log starts with FMT
 * messages (id 128) that give each message type's name, length, field types and field names.
 * The first pass only records where each message starts; fields are decoded when the summary
 * or a chart needs them.
 *
 * API: LVDataflash.looksLike(u8), .parse(buffer) -> summary, .series(type, fields, maxPoints)
 */
var LVDataflash = (function () {
'use strict';

var HEAD1 = 0xA3, HEAD2 = 0x95, FMT_ID = 128;
var SIZES = { a: 64, b: 1, B: 1, h: 2, H: 2, i: 4, I: 4, f: 4, d: 8, n: 4, N: 16, Z: 64,
              c: 2, C: 2, e: 4, E: 4, L: 4, M: 1, q: 8, Q: 8 };
var TEXT = { n: 1, N: 1, Z: 1 };
var INSTANCE_COLS = ['I', 'Inst', 'Instance', 'IMU', 'Id'];

var COPTER_MODES = { 0: 'Stabilize', 1: 'Acro', 2: 'Alt Hold', 3: 'Auto', 4: 'Guided', 5: 'Loiter',
    6: 'RTL', 7: 'Circle', 9: 'Land', 11: 'Drift', 13: 'Sport', 14: 'Flip', 15: 'AutoTune',
    16: 'PosHold', 17: 'Brake', 18: 'Throw', 19: 'Avoid ADSB', 20: 'Guided NoGPS', 21: 'Smart RTL',
    22: 'FlowHold', 23: 'Follow', 24: 'ZigZag', 25: 'SystemID', 26: 'Autorotate', 27: 'Auto RTL',
    28: 'Turtle' };
var ERR_SUBSYS = { 1: 'Main', 2: 'Radio', 3: 'Compass', 4: 'Optical flow', 5: 'Radio failsafe',
    6: 'Battery failsafe', 7: 'GPS failsafe', 8: 'GCS failsafe', 9: 'Fence failsafe', 10: 'Flight mode',
    11: 'GPS', 12: 'Crash check', 13: 'Flip', 14: 'AutoTune', 15: 'Parachute', 16: 'EKF check',
    17: 'EKF/InertialNav failsafe', 18: 'Barometer', 19: 'CPU', 20: 'ADSB failsafe', 21: 'Terrain',
    22: 'Navigation', 23: 'Terrain failsafe', 24: 'EKF primary', 25: 'Thrust loss', 26: 'Sensor failsafe',
    27: 'Leak failsafe', 28: 'Pilot input', 29: 'Vibration failsafe', 30: 'Internal error',
    31: 'Dead-reckoning failsafe' };
var EVENTS = { 10: 'Armed', 11: 'Disarmed', 15: 'Auto armed', 17: 'Land complete (maybe)',
    18: 'Land complete', 19: 'Lost GPS', 21: 'Flip start', 22: 'Flip end', 25: 'Home set',
    28: 'Not landed', 29: 'Lost GPS', 41: 'Motor emergency stop', 42: 'Motor emergency stop cleared',
    43: 'Motor interlock disabled', 44: 'Motor interlock enabled', 54: 'EKF yaw reset',
    56: 'Surfaced', 57: 'Not surfaced', 58: 'Bottom reached', 59: 'Not bottom' };

var log = null;    // {buf, dv, u8, fmts: {id: fmt}, byName: {name: fmt}, offsets: {id: Uint32Array}, counts: {id: n}}

function readText(u8, pos, len) {
    var s = '';
    for (var k = 0; k < len; k++) {
        var c = u8[pos + k];
        if (c === 0) break;
        s += String.fromCharCode(c);
    }
    return s.replace(/\s+$/, '');
}

function readField(dv, u8, pos, t) {
    switch (t) {
        case 'b': return dv.getInt8(pos);
        case 'B': case 'M': return dv.getUint8(pos);
        case 'h': return dv.getInt16(pos, true);
        case 'H': return dv.getUint16(pos, true);
        case 'i': return dv.getInt32(pos, true);
        case 'I': return dv.getUint32(pos, true);
        case 'f': return dv.getFloat32(pos, true);
        case 'd': return dv.getFloat64(pos, true);
        case 'c': return dv.getInt16(pos, true) / 100;
        case 'C': return dv.getUint16(pos, true) / 100;
        case 'e': return dv.getInt32(pos, true) / 100;
        case 'E': return dv.getUint32(pos, true) / 100;
        case 'L': return dv.getInt32(pos, true) / 1e7;
        case 'Q': return dv.getUint32(pos, true) + dv.getUint32(pos + 4, true) * 4294967296;
        case 'q': return dv.getUint32(pos, true) + dv.getInt32(pos + 4, true) * 4294967296;
        case 'n': return readText(u8, pos, 4);
        case 'N': return readText(u8, pos, 16);
        case 'Z': return readText(u8, pos, 64);
        case 'a': return null;
    }
    return null;
}

function makeFmt(id, name, length, format, columns) {
    var cols = columns ? columns.split(',') : [];
    var fields = {}, pos = 3, ok = true;
    for (var k = 0; k < format.length; k++) {
        var t = format.charAt(k), size = SIZES[t];
        if (!size) { ok = false; break; }
        if (cols[k] !== undefined) fields[cols[k]] = { t: t, pos: pos };
        pos += size;
    }
    var inst = null;
    for (var j = 0; j < INSTANCE_COLS.length; j++) {
        if (fields[INSTANCE_COLS[j]] && !TEXT[fields[INSTANCE_COLS[j]].t]) { inst = INSTANCE_COLS[j]; break; }
    }
    return { id: id, name: name, len: length, format: format, cols: cols, fields: fields,
             ok: ok && pos === length, instCol: inst };
}

function get(fmt, off, name) {
    var f = fmt.fields[name];
    return f ? readField(log.dv, log.u8, off + f.pos, f.t) : undefined;
}

/* First pass: find every message start, register FMTs, record offsets per type. */
function index(buf) {
    var u8 = new Uint8Array(buf), dv = new DataView(buf), n = u8.length;
    var fmts = {}, byName = {}, lists = {}, counts = {}, bad = 0;
    fmts[FMT_ID] = makeFmt(FMT_ID, 'FMT', 89, 'BBnNZ', 'Type,Length,Name,Format,Columns');
    var i = 0;
    while (i + 3 <= n) {
        if (u8[i] !== HEAD1 || u8[i + 1] !== HEAD2) { i++; bad++; continue; }
        var id = u8[i + 2], fmt = fmts[id];
        if (!fmt || i + fmt.len > n) { i++; bad++; continue; }
        if (id === FMT_ID) {
            var tId = u8[i + 3], tLen = u8[i + 4];
            var tName = readText(u8, i + 5, 4), tFormat = readText(u8, i + 9, 16), tCols = readText(u8, i + 25, 64);
            if (tId !== FMT_ID) {
                var nf = makeFmt(tId, tName, tLen, tFormat, tCols);
                if (nf.ok) { fmts[tId] = nf; byName[tName] = nf; }
            }
        } else {
            var arr = lists[id];
            if (!arr) { arr = lists[id] = { a: new Uint32Array(1024), n: 0 }; }
            if (arr.n === arr.a.length) { var g = new Uint32Array(arr.a.length * 2); g.set(arr.a); arr.a = g; }
            arr.a[arr.n++] = i;
        }
        i += fmt.len;
    }
    var offsets = {};
    for (var k in lists) {
        if (lists.hasOwnProperty(k)) { offsets[k] = lists[k].a.subarray(0, lists[k].n); counts[k] = lists[k].n; }
    }
    return { buf: buf, u8: u8, dv: dv, fmts: fmts, byName: byName, offsets: offsets, counts: counts, bad: bad, size: n };
}

function each(name, fn) {
    var fmt = log.byName[name];
    if (!fmt) return false;
    var offs = log.offsets[fmt.id];
    if (!offs) return false;
    for (var k = 0; k < offs.length; k++) fn(fmt, offs[k]);
    return true;
}

function haversine(lat1, lon1, lat2, lon2) {
    var R = 6371000, rad = Math.PI / 180;
    var dLat = (lat2 - lat1) * rad, dLon = (lon2 - lon1) * rad;
    var a = Math.sin(dLat / 2) * Math.sin(dLat / 2) +
            Math.cos(lat1 * rad) * Math.cos(lat2 * rad) * Math.sin(dLon / 2) * Math.sin(dLon / 2);
    return 2 * R * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

function severityOf(text) {
    if (/crash|critical|emergency|internal error/i.test(text)) return 'error';
    if (/fail|error|bad|unhealthy|lost|prearm|warning|low|not healthy|glitch|compass variance|ekf variance/i.test(text)) return 'warning';
    return 'info';
}

function summarize() {
    var t0 = null, tEnd = 0;
    function sec(us) {
        if (t0 === null) t0 = us;
        var s = (us - t0) / 1e6;
        if (s > tEnd) tEnd = s;
        return s;
    }
    // establish t0 from the earliest timed message among common types
    var first = Infinity;
    ['MSG', 'PARM', 'GPS', 'ATT', 'IMU', 'BARO', 'MODE', 'EV'].forEach(function (nm) {
        var fmt = log.byName[nm];
        if (fmt && fmt.fields.TimeUS && log.offsets[fmt.id] && log.offsets[fmt.id].length) {
            var v = get(fmt, log.offsets[fmt.id][0], 'TimeUS');
            if (v > 0 && v < first) first = v;
        }
    });
    t0 = first === Infinity ? 0 : first;

    var messages = [], modes = [], armed = [], params = [];
    var version = null, armState = null, armedAt = null;

    each('MSG', function (fmt, off) {
        var text = get(fmt, off, 'Message') || '';
        var t = sec(get(fmt, off, 'TimeUS') || t0);
        if (!version && /(ArduCopter|APM:Copter|ArduPlane|ArduRover)\s*V?\d/i.test(text)) version = text;
        messages.push({ t: t, kind: 'Message', sev: severityOf(text), text: text });
    });
    each('ERR', function (fmt, off) {
        var sub = get(fmt, off, 'Subsys'), code = get(fmt, off, 'ECode');
        var name = ERR_SUBSYS[sub] || ('Subsystem ' + sub);
        messages.push({ t: sec(get(fmt, off, 'TimeUS') || t0), kind: 'Error',
                        sev: code === 0 ? 'info' : 'error',
                        text: name + (code === 0 ? ': cleared' : ': error code ' + code) });
    });
    each('EV', function (fmt, off) {
        var id = get(fmt, off, 'Id'), t = sec(get(fmt, off, 'TimeUS') || t0);
        var name = EVENTS[id] || ('Event ' + id);
        if (log.byName.ARM && (id === 10 || id === 11)) return;      // ARM messages already say this
        messages.push({ t: t, kind: 'Event', sev: (id === 19 || id === 29 || id === 41) ? 'warning' : 'info', text: name });
        if (!log.byName.ARM) {
            if (id === 10 || id === 15) { if (armedAt === null) armedAt = t; }
            if (id === 11 && armedAt !== null) { armed.push([armedAt, t]); armedAt = null; }
        }
    });
    each('ARM', function (fmt, off) {
        var st = get(fmt, off, 'ArmState'), t = sec(get(fmt, off, 'TimeUS') || t0);
        if (st && armedAt === null) armedAt = t;
        if (!st && armedAt !== null) { armed.push([armedAt, t]); armedAt = null; }
        messages.push({ t: t, kind: 'Event', sev: 'info', text: st ? 'Armed' : 'Disarmed' });
    });
    each('MODE', function (fmt, off) {
        var num = get(fmt, off, 'ModeNum');
        if (num === undefined) num = get(fmt, off, 'Mode');
        var name = COPTER_MODES[num] || ('Mode ' + num);
        var t = sec(get(fmt, off, 'TimeUS') || t0);
        modes.push({ t: t, num: num, name: name });
        messages.push({ t: t, kind: 'Mode', sev: 'info', text: 'Flight mode: ' + name });
    });
    each('PARM', function (fmt, off) {
        params.push({ name: get(fmt, off, 'Name'), value: get(fmt, off, 'Value') });
    });

    // last values win for parameters set more than once
    var pmap = {};
    params.forEach(function (p) { pmap[p.name] = p.value; });
    params = Object.keys(pmap).sort().map(function (k) { return { name: k, value: pmap[k] }; });

    // GPS: fix, distance, speed, date
    var home = null, maxDist = 0, path = 0, last = null, maxSpd = 0, minSats = null, maxSats = null,
        maxHdop = null, date = null;
    function inArmed(t) {
        if (!armed.length && armedAt === null) return true;     // no arming in this log: use all of it
        for (var k = 0; k < armed.length; k++) if (t >= armed[k][0] && t < armed[k][1]) return true;
        return armedAt !== null && t >= armedAt;
    }
    var gpsInst = null;
    each('GPS', function (fmt, off) {
        if (fmt.instCol) {
            var inst = get(fmt, off, fmt.instCol);
            if (gpsInst === null) gpsInst = inst;
            if (inst !== gpsInst) return;
        }
        var t = sec(get(fmt, off, 'TimeUS') || t0);
        var status = get(fmt, off, 'Status'), lat = get(fmt, off, 'Lat'), lng = get(fmt, off, 'Lng');
        var sats = get(fmt, off, 'NSats'), hdop = get(fmt, off, 'HDop'), spd = get(fmt, off, 'Spd');
        if (!date && status >= 3) {
            var wk = get(fmt, off, 'GWk'), ms = get(fmt, off, 'GMS');
            if (wk > 0) date = new Date(Date.UTC(1980, 0, 6) + wk * 604800000 + ms - 18000).toISOString();
        }
        if (status < 3 || !lat || !lng) return;
        var flying = inArmed(t);
        if (!home && (flying || armed.length === 0)) home = [lat, lng];
        if (flying) {
            if (home) maxDist = Math.max(maxDist, haversine(home[0], home[1], lat, lng));
            if (last) { var d = haversine(last[0], last[1], lat, lng); if (d < 100) path += d; }
            last = [lat, lng];
            if (spd > maxSpd) maxSpd = spd;
            if (sats !== undefined) { minSats = minSats === null ? sats : Math.min(minSats, sats); maxSats = maxSats === null ? sats : Math.max(maxSats, sats); }
            if (hdop !== undefined) maxHdop = maxHdop === null ? hdop : Math.max(maxHdop, hdop);
        }
    });

    var maxAlt = null;
    each('CTUN', function (fmt, off) {
        var t = sec(get(fmt, off, 'TimeUS') || t0);
        var alt = get(fmt, off, 'Alt');
        if (alt === undefined) alt = get(fmt, off, 'BAlt');
        if (alt !== undefined && inArmed(t)) maxAlt = maxAlt === null ? alt : Math.max(maxAlt, alt);
    });

    var batInst = null, voltStart = null, voltMin = null, currMax = null, mahFirst = null, mahLast = null;
    each('BAT', function (fmt, off) {
        if (fmt.instCol) {
            var inst = get(fmt, off, fmt.instCol);
            if (batInst === null) batInst = inst;
            if (inst !== batInst) return;
        }
        var t = sec(get(fmt, off, 'TimeUS') || t0);
        var v = get(fmt, off, 'Volt'), c = get(fmt, off, 'Curr'), mah = get(fmt, off, 'CurrTot');
        if (v > 0 && voltStart === null && (inArmed(t) || armed.length === 0)) voltStart = v;
        if (v > 0 && inArmed(t)) voltMin = voltMin === null ? v : Math.min(voltMin, v);
        if (c !== undefined && inArmed(t)) currMax = currMax === null ? c : Math.max(currMax, c);
        if (mah !== undefined) { if (mahFirst === null && inArmed(t)) mahFirst = mah; if (inArmed(t)) mahLast = mah; }
    });

    var vibeMax = null, clips = 0;
    each('VIBE', function (fmt, off) {
        var t = sec(get(fmt, off, 'TimeUS') || t0);
        if (!inArmed(t) && armed.length) return;
        ['VibeX', 'VibeY', 'VibeZ'].forEach(function (k) {
            var v = get(fmt, off, k);
            if (v !== undefined) vibeMax = vibeMax === null ? v : Math.max(vibeMax, v);
        });
        var c = (get(fmt, off, 'Clip') || 0) + (get(fmt, off, 'Clip0') || 0) + (get(fmt, off, 'Clip1') || 0) + (get(fmt, off, 'Clip2') || 0);
        if (c > clips) clips = c;
    });

    // end time: scan the last messages of common types
    ['ATT', 'IMU', 'GPS', 'BAT', 'CTUN', 'BARO'].forEach(function (nm) {
        var fmt = log.byName[nm];
        if (fmt && fmt.fields.TimeUS && log.offsets[fmt.id] && log.offsets[fmt.id].length) {
            var o = log.offsets[fmt.id];
            sec(get(fmt, o[o.length - 1], 'TimeUS'));
        }
    });
    if (armedAt !== null) armed.push([armedAt, tEnd]);
    var flightTime = 0;
    armed.forEach(function (r) { flightTime += Math.max(0, r[1] - r[0]); });

    messages.sort(function (a, b) { return a.t - b.t; });

    // types and numeric fields for the chart picker
    var types = [];
    Object.keys(log.byName).sort().forEach(function (nm) {
        var fmt = log.byName[nm], c = log.counts[fmt.id] || 0;
        if (!c || !fmt.fields.TimeUS) return;
        var numeric = fmt.cols.filter(function (col) {
            var f = fmt.fields[col];
            return f && col !== 'TimeUS' && !TEXT[f.t] && f.t !== 'a';
        });
        if (numeric.length) types.push({ name: nm, count: c, fields: numeric });
    });

    return {
        size: log.size, skippedBytes: log.bad, duration: tEnd, version: version, date: date,
        flightTime: flightTime, armed: armed, modes: modes, messages: messages, params: params,
        maxAlt: maxAlt, maxDist: home ? maxDist : null, path: home ? path : null, maxSpeed: maxSpd || null,
        sats: [minSats, maxSats], maxHdop: maxHdop,
        battery: { start: voltStart, min: voltMin, maxCurrent: currMax,
                   used: (mahFirst !== null && mahLast !== null) ? mahLast - mahFirst : null },
        vibration: { max: vibeMax, clips: clips },
        types: types, t0: t0
    };
}

function series(type, fields, maxPoints) {
    var fmt = log.byName[type];
    if (!fmt || !log.offsets[fmt.id]) throw new Error('No ' + type + ' messages in this log');
    var offs = log.offsets[fmt.id], t0 = log.summary.t0, inst = null;
    var ts = [], ys = fields.map(function () { return []; });
    for (var k = 0; k < offs.length; k++) {
        var off = offs[k];
        if (fmt.instCol) {
            var v = get(fmt, off, fmt.instCol);
            if (inst === null) inst = v;
            if (v !== inst) continue;
        }
        ts.push((get(fmt, off, 'TimeUS') - t0) / 1e6);
        for (var j = 0; j < fields.length; j++) {
            var y = get(fmt, off, fields[j]);
            ys[j].push(typeof y === 'number' && isFinite(y) ? y : NaN);
        }
    }
    // min/max decimation per bucket keeps spikes visible
    var n = ts.length, out = [];
    for (var j2 = 0; j2 < fields.length; j2++) {
        var t = [], y2 = [];
        if (n <= maxPoints) { t = ts; y2 = ys[j2]; }
        else {
            var buckets = Math.floor(maxPoints / 2), size = n / buckets;
            for (var b = 0; b < buckets; b++) {
                var s = Math.floor(b * size), e = Math.min(n, Math.floor((b + 1) * size));
                var lo = Infinity, hi = -Infinity, ilo = s, ihi = s;
                for (var q = s; q < e; q++) {
                    var val = ys[j2][q];
                    if (val < lo) { lo = val; ilo = q; }
                    if (val > hi) { hi = val; ihi = q; }
                }
                if (lo === Infinity) continue;
                if (ilo <= ihi) { t.push(ts[ilo], ts[ihi]); y2.push(lo, hi); }
                else { t.push(ts[ihi], ts[ilo]); y2.push(hi, lo); }
            }
        }
        out.push({ name: type + '.' + fields[j2], t: Float64Array.from(t), y: Float64Array.from(y2) });
    }
    return out;
}

return {
    looksLike: function (u8) { return u8.length > 3 && u8[0] === HEAD1 && u8[1] === HEAD2; },
    parse: function (buffer) {
        log = index(buffer);
        if (!Object.keys(log.byName).length) throw new Error('This is not an ArduPilot flight log (no FMT messages found).');
        log.summary = summarize();
        log.summary.kind = 'bin';
        return log.summary;
    },
    series: function (type, fields, maxPoints) {
        if (!log) throw new Error('No log loaded');
        return series(type, fields, maxPoints);
    },
    // for tools/test_logviewer.js
    index: index, makeFmt: makeFmt, readField: readField, severityOf: severityOf,
    _setLog: function (l) { log = l; },
    summarize: function () { log.summary = summarize(); return log.summary; },
    seriesRaw: series, COPTER_MODES: COPTER_MODES
};
})();

if (typeof module !== 'undefined' && module.exports) module.exports = LVDataflash;
