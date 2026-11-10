/* Solo Web Utility flight log viewer: the only code that talks to the Solo.
 * Read-only calls to /api/logviewer/* (modules/log_viewer/log_viewer_api.py); uses the
 * utility's login cookie, nothing else from the page. */
(function (LV) {
    'use strict';

    function readError(res) {
        return res.text().then(function (txt) {
            var msg = 'HTTP ' + res.status;
            try { msg = JSON.parse(txt).error || msg; } catch (e) { /* not JSON */ }
            if (res.status === 401) msg = 'Your login has expired: reload the page and log in again.';
            throw new Error(msg);
        });
    }

    LV.parts.api = {
        listFlightLogs: function () {
            return fetch('/api/logviewer/flightlogs', { credentials: 'same-origin', cache: 'no-store' })
                .then(function (res) { return res.ok ? res.json() : readError(res); });
        },

        logUrl: function (n) {
            return '/api/logviewer/flightlog/' + encodeURIComponent(n);
        },

        /* Fetch one log as an ArrayBuffer; onProgress(received, total) while it downloads. */
        fetchFlightLog: function (n, onProgress) {
            return fetch(this.logUrl(n), { credentials: 'same-origin', cache: 'no-store' }).then(function (res) {
                if (!res.ok) return readError(res);
                var total = parseInt(res.headers.get('Content-Length') || '0', 10);
                if (!res.body || !res.body.getReader) return res.arrayBuffer();
                var reader = res.body.getReader(), chunks = [], got = 0;
                function pump() {
                    return reader.read().then(function (r) {
                        if (r.done) {
                            var out = new Uint8Array(got), pos = 0;
                            chunks.forEach(function (c) { out.set(c, pos); pos += c.length; });
                            return out.buffer;
                        }
                        chunks.push(r.value);
                        got += r.value.length;
                        if (onProgress) onProgress(got, total);
                        return pump();
                    });
                }
                return pump();
            });
        }
    };
})(window.SoloLogViewer);
