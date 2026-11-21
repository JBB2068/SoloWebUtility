/* Solo Web Utility flight log viewer: the Web Worker that reads logs.
 *
 * Runs off the page's main thread, so a big or damaged log cannot freeze the utility, and an
 * exception here only reaches the viewer (as an error reply), never the rest of the page.
 * Picks the reader by looking at the file: .BIN (DataFlash, from the Cube) or .tlog (MAVLink
 * telemetry, recorded by the controller). A .tlog can be 100 MB, so it is read as a stream while it
 * downloads and never held whole in memory.
 *
 * In:  {cmd: "parse", id, buffer}                a file the user opened
 *      {cmd: "parseUrl", id, url}                a log on the Solo or the controller (streamed)
 *      {cmd: "series", id, type, fields, maxPoints}
 * Out: {id, progress: {got, total}} while downloading, then {id, ok: true, result} | {id, ok: false, error}
 */
'use strict';
var q = (self.location && self.location.search) || '';
importScripts('dataflash.js' + q, 'tlog.js' + q);

var reader = null;
var NOT_A_LOG = 'This is not an ArduPilot .BIN flight log or a MAVLink .tlog telemetry log.';

function pick(u8) {
    return LVDataflash.looksLike(u8) ? LVDataflash : LVTlog.looksLike(u8) ? LVTlog : null;
}

function httpError(res) {
    return res.text().then(function (txt) {
        var msg = 'HTTP ' + res.status;
        try { msg = JSON.parse(txt).error || msg; } catch (e) { /* not JSON */ }
        if (res.status === 401) msg = 'Your login has expired: reload the page and log in again.';
        throw new Error(msg);
    });
}

function concat(parts, total) {
    var out = new Uint8Array(total), pos = 0;
    parts.forEach(function (c) { out.set(c, pos); pos += c.length; });
    return out;
}

function parseUrl(msg) {
    return fetch(msg.url, { credentials: 'same-origin', cache: 'no-store' }).then(function (res) {
        if (!res.ok) return httpError(res);
        var total = parseInt(res.headers.get('Content-Length') || '0', 10);
        if (!res.body || !res.body.getReader) {
            return res.arrayBuffer().then(function (buf) {
                reader = pick(new Uint8Array(buf, 0, Math.min(buf.byteLength, 4096)));
                if (!reader) throw new Error(NOT_A_LOG);
                return reader.parse(buf);
            });
        }
        var rd = res.body.getReader(), got = 0, head = [], headLen = 0, kept = null, lastPost = 0;
        reader = null;
        function begin() {
            var first = concat(head, headLen);
            reader = pick(first);
            if (!reader) throw new Error(NOT_A_LOG);
            if (reader === LVTlog) { LVTlog.begin(); LVTlog.feed(first); } else { kept = [first]; }
            head = null;
        }
        function pump() {
            return rd.read().then(function (r) {
                if (r.done) {
                    if (!reader) begin();
                    if (reader === LVTlog) return LVTlog.finish();
                    var n = 0;
                    kept.forEach(function (c) { n += c.length; });
                    var all = concat(kept, n);
                    kept = null;
                    return LVDataflash.parse(all.buffer);
                }
                got += r.value.length;
                if (!reader) {
                    head.push(r.value); headLen += r.value.length;
                    if (headLen >= 4096) begin();
                } else if (reader === LVTlog) {
                    LVTlog.feed(r.value);
                } else {
                    kept.push(r.value);
                }
                var now = Date.now();
                if (now - lastPost > 250) { lastPost = now; self.postMessage({ id: msg.id, progress: { got: got, total: total } }); }
                return pump();
            });
        }
        return pump();
    });
}

self.onmessage = function (ev) {
    var msg = ev.data || {};
    function ok(result) { self.postMessage({ id: msg.id, ok: true, result: result }); }
    function fail(e) { self.postMessage({ id: msg.id, ok: false, error: String(e && e.message || e) }); }
    try {
        if (msg.cmd === 'parse') {
            var u8 = new Uint8Array(msg.buffer);
            reader = pick(u8);
            if (!reader) throw new Error(NOT_A_LOG);
            ok(reader.parse(msg.buffer));
        } else if (msg.cmd === 'parseUrl') {
            parseUrl(msg).then(ok, fail);
        } else if (msg.cmd === 'series') {
            if (!reader) throw new Error('No log loaded');
            ok(reader.series(msg.type, msg.fields, msg.maxPoints || 2000));
        } else {
            throw new Error('Unknown command ' + msg.cmd);
        }
    } catch (e) {
        fail(e);
    }
};
