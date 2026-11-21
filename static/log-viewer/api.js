/* Solo Web Utility flight log viewer: the only code that talks to the Solo (or the controller).
 * Read-only calls to /api/logviewer/* (modules/log_viewer/log_viewer_api.py on the Solo,
 * modules/log_viewer/controller/log_server.py on the controller, which answers the same paths);
 * uses the utility's login cookie, nothing else from the page (on a 401 it fires the utility's solo-auth-required event so the login box opens). Installing or removing the viewer on
 * the controller needs the header X-Log-Viewer so that no other web page can trigger it. */
(function (LV) {
    'use strict';

    function readError(res) {
        return res.text().then(function (txt) {
            var msg = 'HTTP ' + res.status;
            try { msg = JSON.parse(txt).error || msg; } catch (e) { /* not JSON */ }
            if (res.status === 401) {
                msg = 'Your login has expired: log in again (the login box opens), then press Refresh.';
                /* the utility's own hook (the same hook the dashboard uses): opens its login box, which renews the cookie */
                try { window.dispatchEvent(new Event('solo-auth-required')); } catch (e) { /* old browser */ }
            }
            throw new Error(msg);
        });
    }

    function getJson(url, headers) {
        return fetch(url, { credentials: 'same-origin', cache: 'no-store', headers: headers || {} })
            .then(function (res) { return res.ok ? res.json() : readError(res); });
    }

    LV.parts.api = {
        listFlightLogs: function () {
            return getJson('/api/logviewer/flightlogs');
        },

        /* The URL a log is read from: ids are "12" (a .BIN on the Solo), "t0".."t9" or "b12" (controller). */
        logUrl: function (id) {
            return '/api/logviewer/flightlog/' + encodeURIComponent(id);
        },

        /* The controller's own viewer (only the Solo's utility answers these). */
        controllerStatus: function () {
            return getJson('/api/logviewer/controller');
        },
        controllerInstall: function () {
            return getJson('/api/logviewer/controller/install', { 'X-Log-Viewer': '1' });
        },
        controllerRemove: function () {
            return getJson('/api/logviewer/controller/remove', { 'X-Log-Viewer': '1' });
        },

        /* Logs on the Cube's SD card (the Solo's utility only). cubeStatus never contacts the Cube; the
         * others start a job that the page then follows with cubeStatus. They need the header. */
        cubeStatus: function () { return getJson('/api/logviewer/cubesd'); },
        cubeList: function () { return getJson('/api/logviewer/cubesd/list', { 'X-Log-Viewer': '1' }); },
        cubeCopy: function (n) { return getJson('/api/logviewer/cubesd/copy/' + encodeURIComponent(n), { 'X-Log-Viewer': '1' }); },
        cubeCancel: function () { return getJson('/api/logviewer/cubesd/cancel', { 'X-Log-Viewer': '1' }); }
    };
})(window.SoloLogViewer);
