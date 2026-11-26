/**
 * 🛸 3DR Solo Cockpit Flight Deck - Overlay HUD & Metric/Imperial JS Engine
 * Full-bleed Artificial Horizon, Floating Translucent HUD, Unit Toggles, MAVLink Console.
 */

(function() {
    'use strict';

    // Unit Preference ('metric' or 'imperial')
    let unitSystem = localStorage.getItem('solo_unit_pref') || 'metric';

    let currentTelem = null;
    let voiceEnabled = true;
    let hudVisible = true;
    let lastSpokenMode = "";
    let lastSpokenArmed = null;
    let lastSpokenLowBat = false;
    let lastSpokenCritBat = false;
    let lastRenderedMsgKey = "";
    // Login token (saved by the main page at login). Every Cockpit request sends it in a
    // header; on 401 the main page shows its login box.
    function authHeaders(extra) {
        const h = extra || {};
        try {
            const t = localStorage.getItem('solo_auth_token');
            if (t) h['X-Auth-Token'] = t;
        } catch (e) {}
        return h;
    }
    function authRequired() {
        try { window.dispatchEvent(new Event('solo-auth-required')); } catch (e) {}
    }
    function postAction(body) {
        return fetch('/api/action', {
            method: 'POST',
            headers: authHeaders({ 'Content-Type': 'application/json' }),
            body: JSON.stringify(body)
        }).then(function(r) {
            if (r.status === 401) { authRequired(); throw new Error('Login required'); }
            return r.json();
        });
    }

    let msgLog = [];                // cached MAVLink message log (telemetry sends it only when it changes)
    let msgLogSeq = -1;
    let hudDirty = true;            // redraw the HUD canvas only when something changed
    const VIDEO_FPS = 24;           // Solo HDMI encoder frame rate
    const VIDEO_MAX_LAG_S = 0.5;    // jump to the newest frame when playback falls further behind
    const VIDEO_FINDER_MAX_LAG_S = 0.15;    // ... while the pad finder or TAKEOFF SPOT is on: catch up to this
    const VIDEO_CATCHUP_RATE = 1.25;        // ... by playing a little faster (a jump stalls the decoder)
    // OSD messages bar state
    let osdSession = null;
    let osdLastId = 0;
    let osdNewTimer = null;
    let osdLayoutKey = '';
    const OSD_ACTIVE_SECS_INFO = 10;    // notice / info / debug: bright for 10 s, then dimmed
    const OSD_ACTIVE_SECS_ALERT = 30;   // warning and worse (PreArm, failsafe...): bright for 30 s

    // DOM Elements
    const pfdCanvas = document.getElementById('pfdCanvas');
    const hsiCanvas = document.getElementById('hsiMiniCanvas');
    const pfdCtx = pfdCanvas.getContext('2d');
    const hsiCtx = hsiCanvas.getContext('2d');

    // Annunciators
    const armPill = document.getElementById('armPill');
    const modePill = document.getElementById('modePill');
    const batPill = document.getElementById('batPill');
    const gpsPill = document.getElementById('gpsPill');
    const linkPill = document.getElementById('linkPill');
    const linkDot = document.getElementById('linkDot');
    const linkTxt = document.getElementById('linkTxt');
    const btnUnitToggle = document.getElementById('btnUnitToggle');

    // Live Video Elements & State (HDMI from Copter i.MX6)
    let videoModeActive = true;
    let jmuxer = null;
    let videoActive = false;
    let lastVideoPacketTime = 0;

    const liveVideoFeed = document.getElementById('liveVideoFeed');
    const videoStandbyOverlay = document.getElementById('videoStandbyOverlay');
    const videoSignalBadge = document.getElementById('videoSignalBadge');
    const btnVideoToggle = document.getElementById('btnVideoToggle');
    const videoPill = document.getElementById('videoPill');
    const videoDot = document.getElementById('videoDot');
    const videoTxt = document.getElementById('videoTxt');

    // Left HUD Elements
    const hsiHdgTxt = document.getElementById('hsiHdgTxt');
    const homeDistVal = document.getElementById('homeDistVal');
    const homeBrgVal = document.getElementById('homeBrgVal');
    const gpsFixTxt = document.getElementById('gpsFixTxt');
    const gpsSatsCount = document.getElementById('gpsSatsCount');
    const gpsLatVal = document.getElementById('gpsLatVal');
    const gpsLonVal = document.getElementById('gpsLonVal');
    const altMslVal = document.getElementById('altMslVal');
    const gpsHdopVal = document.getElementById('gpsHdopVal');
    const rtkRow = document.getElementById('rowRtk');
    const rtkBaseVal = document.getElementById('rtkBaseVal');
    const rtkSetupRow = document.getElementById('rowRtkSetup');
    const rtkMsgsRow = document.getElementById('rowRtkMsgs');
    const rtkMsgsVal = document.getElementById('rtkMsgsVal');
    const rtkSetupNote = document.getElementById('rtkSetupNote');
    const btnRtkSetup = document.getElementById('btnRtkSetup');
    const btnRtkUsb = document.getElementById('btnRtkUsb');
    const mavlinkMsgBox = document.getElementById('mavlinkMsgBox');
    const msgCountBadge = document.getElementById('msgCountBadge');
    const btnScrollBottom = document.getElementById('btnScrollBottom');
    const btnExpandMsgs = document.getElementById('btnExpandMsgs');
    const btnClearMsgs = document.getElementById('btnClearMsgs');
    const btnToggleCompass = document.getElementById('btnToggleCompass');
    const cardCompass = document.getElementById('cardCompass');

    function escapeHtml(str) {
        if (!str) return '';
        return String(str)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }

    // Right HUD Elements
    const batTempTxt = document.getElementById('batTempTxt');
    const batVoltsBig = document.getElementById('batVoltsBig');
    const batPctBig = document.getElementById('batPctBig');
    const batBarFill = document.getElementById('batBarFill');
    const batAmpsVal = document.getElementById('batAmpsVal');
    const batConsumedVal = document.getElementById('batConsumedVal');
    const flightTimeVal = document.getElementById('flightTimeVal');
    const c1Val = document.getElementById('c1Val');
    const c2Val = document.getElementById('c2Val');
    const c3Val = document.getElementById('c3Val');
    const c4Val = document.getElementById('c4Val');
    const pktRateTxt = document.getElementById('pktRateTxt');
    const rcRssiVal = document.getElementById('rcRssiVal');
    const vibeVal = document.getElementById('vibeVal');
    const airspeedHudVal = document.getElementById('airspeedHudVal');
    const groundspeedHudVal = document.getElementById('groundspeedHudVal');
    const btnArm = document.getElementById('btnArm');

    // Panels
    const leftHud = document.getElementById('leftHud');
    const rightHud = document.getElementById('rightHud');

    // =========================================================================
    // Metric / Imperial Conversion Utilities
    // =========================================================================
    function getConvertedAlt(meters) {
        if (unitSystem === 'imperial') {
            return { val: (meters * 3.28084), unit: 'ft' };
        }
        return { val: meters, unit: 'm' };
    }

    function getConvertedSpeed(mps) {
        if (unitSystem === 'imperial') {
            return { val: (mps * 2.23694), unit: 'mph' };
        }
        return { val: mps, unit: 'm/s' };
    }

    function getConvertedDist(meters) {
        if (unitSystem === 'imperial') {
            const feet = meters * 3.28084;
            if (feet >= 5280) {
                return { val: (feet / 5280).toFixed(2), unit: 'mi' };
            }
            return { val: feet.toFixed(1), unit: 'ft' };
        }
        if (meters >= 1000) {
            return { val: (meters / 1000).toFixed(2), unit: 'km' };
        }
        return { val: meters.toFixed(1), unit: 'm' };
    }

    function updateUnitButtonLabel() {
        if (unitSystem === 'imperial') {
            btnUnitToggle.textContent = '🇺🇸 IMPERIAL';
            btnUnitToggle.style.color = 'var(--accent-amber)';
            btnUnitToggle.style.borderColor = 'var(--accent-amber)';
        } else {
            btnUnitToggle.textContent = '🌐 METRIC';
            btnUnitToggle.style.color = 'var(--accent-blue)';
            btnUnitToggle.style.borderColor = 'var(--accent-blue)';
        }
        document.querySelectorAll('.unit-alt').forEach(el => el.textContent = unitSystem === 'imperial' ? 'ft' : 'm');
        document.querySelectorAll('.unit-spd').forEach(el => el.textContent = unitSystem === 'imperial' ? 'mph' : 'm/s');
        document.querySelectorAll('.unit-dist').forEach(el => el.textContent = unitSystem === 'imperial' ? 'ft' : 'm');
    }

    btnUnitToggle.addEventListener('click', function() {
        unitSystem = (unitSystem === 'metric') ? 'imperial' : 'metric';
        hudDirty = true;
        localStorage.setItem('solo_unit_pref', unitSystem);
        updateUnitButtonLabel();
        showPadSettings();
        if (currentTelem) updateDOM(currentTelem);
    });

    updateUnitButtonLabel();

    // =========================================================================
    // Responsive Canvas Resizing
    // =========================================================================
    function resizeCanvases() {
        hudDirty = true;
        const dpr = window.devicePixelRatio || 1;
        const pfdRect = pfdCanvas.parentElement.getBoundingClientRect();
        
        pfdCanvas.width = Math.floor(pfdRect.width * dpr);
        pfdCanvas.height = Math.floor(pfdRect.height * dpr);
        pfdCtx.setTransform(1, 0, 0, 1, 0, 0);
        pfdCtx.scale(dpr, dpr);

        const hsiW = hsiCanvas.clientWidth || 75;
        const hsiH = hsiCanvas.clientHeight || 75;
        hsiCanvas.width = Math.floor(hsiW * dpr);
        hsiCanvas.height = Math.floor(hsiH * dpr);
        hsiCtx.setTransform(1, 0, 0, 1, 0, 0);
        hsiCtx.scale(dpr, dpr);
    }

    window.addEventListener('resize', resizeCanvases);
    window.resizeCockpit = resizeCanvases;
    setTimeout(resizeCanvases, 100);

    document.querySelectorAll('.nav-tab').forEach(function(tab) {
        tab.addEventListener('click', function() {
            setTimeout(resizeCanvases, 50);
            setTimeout(resizeCanvases, 250);
        });
    });

    // =========================================================================
    // Voice Alerts (Web Speech API)
    // =========================================================================
    function speak(text) {
        if (!voiceEnabled || !('speechSynthesis' in window)) return;
        try {
            window.speechSynthesis.cancel();
            const u = new SpeechSynthesisUtterance(text);
            u.rate = 1.05;
            u.volume = 0.85;
            window.speechSynthesis.speak(u);
        } catch (e) {}
    }

    const btnVoice = document.getElementById('btnVoice') || document.getElementById('btnVoiceToggle');
    if (btnVoice) {
        btnVoice.addEventListener('click', function() {
            voiceEnabled = !voiceEnabled;
            this.classList.toggle('active', voiceEnabled);
            if (voiceEnabled) speak("Voice alerts active");
        });
    }

    const btnToggleHud = document.getElementById('btnToggleHud');
    if (btnToggleHud) {
        btnToggleHud.addEventListener('click', function() {
            hudVisible = !hudVisible;
            hudDirty = true;
            this.classList.toggle('active', hudVisible);
            if (leftHud) leftHud.classList.toggle('collapsed', !hudVisible);
            if (rightHud) rightHud.classList.toggle('collapsed', !hudVisible);
        });
    }

    
    const btnRestartVideo = document.getElementById('btnRestartVideo');
    if (btnRestartVideo) {
        btnRestartVideo.addEventListener('click', function(e) {
            e.stopPropagation();
            this.textContent = '⏳ RESTARTING...';
            this.classList.add('active');
            fetch('/api/video/restart', { method: 'POST', headers: authHeaders({ 'Content-Type': 'application/json' }), body: '{}' })
                .then(r => { if (r.status === 401) { authRequired(); } return r.json(); })
                .then(() => {
                    setTimeout(() => {
                        btnRestartVideo.textContent = '🔄 RESTART VIDEO';
                        btnRestartVideo.classList.remove('active');
                    }, 2000);
                });
        });
    }

    // =========================================================================
    // Smart Shots: the SMART SHOTS button opens a row with one button per shot.
    // The server starts the shot through the Solo's shot manager; it is then flown
    // with the Controller, and Fly (or END SHOT) ends it.
    // =========================================================================
    const btnSmartShots = document.getElementById('btnSmartShots');
    const smartShotRow = document.getElementById('smartShotRow');
    const btnEndShot = document.getElementById('btnEndShot');
    const smartShotMsg = document.getElementById('smartShotMsg');
    const shotButtons = smartShotRow ? smartShotRow.querySelectorAll('.btn-shot') : [];
    let smartShotBusy = false;
    let lastSmartShot = null;

    function setSmartShotMsg(text, isError) {
        if (!smartShotMsg) return;
        smartShotMsg.textContent = text;
        smartShotMsg.classList.toggle('error', !!isError);
        layoutShotRow();
    }

    // While a row (Smart Shots or Precision) is open the side HUD panels start below it instead of under it
    function layoutShotRow() {
        const row = [smartShotRow, document.getElementById('precisionRow')].filter(function(r) {
            return r && !r.hidden;
        })[0];
        [leftHud, rightHud].forEach(function(p) {
            if (!p) return;
            if (!row || !p.offsetParent) {
                p.style.removeProperty('top');
                return;
            }
            const top = row.getBoundingClientRect().bottom - p.offsetParent.getBoundingClientRect().top + 6;
            p.style.setProperty('top', Math.round(top) + 'px', 'important');
        });
    }
    window.addEventListener('resize', layoutShotRow);

    function setSmartShotLabel(runningName) {
        if (!btnSmartShots || !smartShotRow) return;
        const caret = smartShotRow.hidden ? ' ▾' : ' ▴';
        btnSmartShots.textContent = '🎬 ' + (runningName ? runningName.toUpperCase() : 'SMART SHOTS') + caret;
    }

    function setShotButtonsDisabled(disabled) {
        shotButtons.forEach(function(b) {
            if (!b.dataset.needs) b.disabled = disabled;
        });
    }

    if (btnSmartShots && smartShotRow) {
        btnSmartShots.addEventListener('click', function(e) {
            e.stopPropagation();
            const open = smartShotRow.hidden;
            smartShotRow.hidden = !open;
            btnSmartShots.setAttribute('aria-expanded', open ? 'true' : 'false');
            setSmartShotLabel(btnSmartShots.classList.contains('shot-running') ? btnSmartShots.dataset.shotName : '');
            if (open) closePrecisionRow();
            layoutShotRow();
            layoutOsdBar();             // the OSD bar moves below the shot row
        });
    }

    shotButtons.forEach(function(btn) {
        btn.addEventListener('click', function(e) {
            e.stopPropagation();
            if (btn.dataset.needs) {
                setSmartShotMsg(btn.dataset.needs, true);
                return;
            }
            if (smartShotBusy) return;
            const shotId = parseInt(btn.dataset.shot, 10);
            const name = btn.textContent.trim();
            smartShotBusy = true;
            setShotButtonsDisabled(true);
            setSmartShotMsg('Starting ' + name + '...', false);
            postAction({ action: 'start_shot', mode: shotId }).then(function(data) {
                const ok = data && data.status === 'ok';
                setSmartShotMsg((data && data.message) || (name + (ok ? ' started.' : ' did not start.')), !ok);
                if (ok) speak('Smart shot ' + name.toLowerCase());
            }).catch(function(err) {
                setSmartShotMsg('Could not start ' + name + ': ' + err.message, true);
            }).then(function() {
                smartShotBusy = false;
                setShotButtonsDisabled(false);
            });
        });
    });

    if (btnEndShot) {
        btnEndShot.addEventListener('click', function(e) {
            e.stopPropagation();
            postAction({ action: 'end_shot' }).then(function(data) {
                const ok = data && data.status === 'ok';
                setSmartShotMsg((data && data.message) || 'End Shot failed.', !ok);
            }).catch(function(err) {
                setSmartShotMsg('End Shot failed: ' + err.message + '. Press Fly on the Controller.', true);
            });
        });
    }

    // Follow: the SoloLink Viewer app hands the page each GPS fix of the phone
    // (window.soloViewerPilotFix); the server carries it on to the shot manager while Follow runs.
    const btnFollow = document.getElementById('btnFollow');
    const btnFollowStyle = document.getElementById('btnFollowStyle');
    const followNeedsDefault = btnFollow ? btnFollow.dataset.needs : '';
    const FOLLOW_STYLES = ['hold', 'leash', 'freelook'];
    const FOLLOW_STYLE_LABELS = { hold: 'HOLD ANGLE', leash: 'LEASH', freelook: 'FREE LOOK' };
    let pilotFixBusy = false;
    let pilotFixPending = null;
    let pilotFixRejected = '';               // why the server refused the last fix ('' if it took it)
    let viewerGpsStatus = '';                // the SoloLink Viewer app's GPS state ('' outside the app)
    const inAndroidWebView = /; wv\)/.test(navigator.userAgent);
    const VIEWER_GPS_PROBLEMS = {
        no_permission: 'the SoloLink Viewer app may not use the phone\'s location. Allow it: Android Settings → Apps → ' +
            'SoloLink Viewer → Permissions → Location → Allow, with Use precise location on',
        approximate: 'the SoloLink Viewer app only has approximate location. Turn on Use precise location: Android ' +
            'Settings → Apps → SoloLink Viewer → Permissions → Location',
        off: 'Location is turned off on the phone. Turn it on (swipe down from the top of the screen)',
        unavailable: 'this phone\'s GPS is not available to the SoloLink Viewer app',
        waiting: 'the phone is still looking for GPS satellites. Stand in the open with a clear view of the sky; ' +
            'without internet the first fix can take a minute or more'
    };
    window.soloViewerGpsStatus = function(status) {
        viewerGpsStatus = String(status || '');
    };

    function sendPilotFix(fix) {
        if (pilotFixBusy) {                  // keep only the newest; it goes as soon as this one is done
            pilotFixPending = fix;
            return;
        }
        pilotFixBusy = true;
        fetch('/api/follow/location', {
            method: 'POST',
            headers: authHeaders({ 'Content-Type': 'application/json' }),
            body: JSON.stringify(fix)
        }).then(function(r) {
            if (r.status === 401) {
                authRequired();
                pilotFixRejected = 'log in to the utility first';
                return null;
            }
            return r.json();
        }).then(function(data) {
            if (data) pilotFixRejected = data.ok ? '' : 'the phone\'s position was refused: ' + data.message;
        }).catch(function(err) {
            pilotFixRejected = 'could not send the phone\'s position (' + err.message + ')';
        }).then(function() {
            pilotFixBusy = false;
            const next = pilotFixPending;
            pilotFixPending = null;
            if (next) sendPilotFix(next);
        });
    }
    window.soloViewerPilotFix = function(fix) {
        if (fix && typeof fix.lat === 'number' && typeof fix.lon === 'number') sendPilotFix(fix);
    };

    // RTK base: the SoloLink Viewer app reads RTCM corrections from a GNSS base receiver on the phone's
    // USB port and hands whole RTCM frames to the page (window.soloViewerRtcm, base64, about once a second).
    // They are posted to /api/rtk/rtcm and the server sends them to the Cube as GPS_RTCM_DATA.
    // Corrections are only useful while fresh: on a backlog the oldest are dropped, a failed post is not retried.
    const RTK_QUEUE_MAX = 400;               // frames waiting (a base sends a few a second)
    const RTK_POST_MAX = 200;                // frames per post (the server takes at most 200)
    let rtkQueue = [];
    let rtkBusy = false;
    let rtkApp = null;                       // {state, device, message} from the Viewer app (null outside it)
    let rtkSendProblem = '';
    let rtkSetupResult = '';
    window.soloViewerRtkStatus = function(st) {
        rtkApp = (st && typeof st === 'object') ? st : { state: String(st || '') };
    };
    function sendRtk() {
        if (rtkBusy || !rtkQueue.length) return;
        rtkBusy = true;
        const frames = rtkQueue.splice(0, RTK_POST_MAX);
        fetch('/api/rtk/rtcm', {
            method: 'POST',
            headers: authHeaders({ 'Content-Type': 'application/json' }),
            body: JSON.stringify({ frames: frames, app: rtkApp || {} })
        }).then(function(r) {
            if (r.status === 401) {
                authRequired();
                rtkSendProblem = 'log in to the utility first';
                return null;
            }
            return r.json();
        }).then(function(data) {
            if (data) rtkSendProblem = data.ok ? '' : String(data.message || 'refused');
        }).catch(function(err) {
            rtkSendProblem = 'could not reach the Solo (' + err.message + ')';
        }).then(function() {
            rtkBusy = false;
            if (rtkQueue.length) setTimeout(sendRtk, 0);
        });
    }
    window.soloViewerRtcm = function(batch) {
        const frames = (batch && Array.isArray(batch.frames)) ? batch.frames : [];
        for (let i = 0; i < frames.length; i++) {
            if (typeof frames[i] === 'string' && frames[i]) rtkQueue.push(frames[i]);
        }
        if (rtkQueue.length > RTK_QUEUE_MAX) rtkQueue.splice(0, rtkQueue.length - RTK_QUEUE_MAX);
        sendRtk();
    };

    // RTK row of the GPS card: what the base and the corrections are doing
    const RTK_APP_TEXT = {
        none: ['PLUG IN BASE', 'No GNSS base receiver on the phone\'s USB port.'],
        permission: ['ALLOW USB', 'Allow the SoloLink Viewer app to use the base receiver (Android asks once).'],
        denied: ['USB NOT ALLOWED', 'Android did not allow the app to use the base. Unplug it and plug it in again, then tap OK.'],
        error: ['BASE ERROR', 'The app could not read the base receiver.'],
        blocked: ['ALLOW USB IN CHROME', 'Chrome only allows USB on secure pages: allow http://10.1.1.10 in chrome://flags.'],
        waiting: ['NO RTCM YET', 'The base is connected but sends no RTCM corrections. Press SET UP BASE (Mission Planner does the same ' +
            'when it connects), or turn on RTCM3 output on USB in u-center and save it in the receiver.']
    };
    // Survey-in choices: at least this long AND until the base knows its own position this well.
    // The first is Mission Planner's default. Tapping the note picks the next one (remembered on this device).
    const RTK_SURVEY_PRESETS = [
        { secs: 60, acc: 2.0, label: '1 MIN · 2 M' },
        { secs: 300, acc: 1.0, label: '5 MIN · 1 M' },
        { secs: 600, acc: 0.5, label: '10 MIN · 0.5 M' },
        { secs: 1800, acc: 0.3, label: '30 MIN · 0.3 M' }
    ];
    let rtkSurveyIdx = 0;
    try {
        const saved = parseInt(localStorage.getItem('rtkSurveyPreset'), 10);
        if (saved >= 0 && saved < RTK_SURVEY_PRESETS.length) rtkSurveyIdx = saved;
    } catch (e) { /* no storage: use the default */ }
    let rtkNoteTimer = null;
    function rtkSurvey() { return RTK_SURVEY_PRESETS[rtkSurveyIdx]; }
    function showRtkNote(text) {
        if (!rtkSetupNote) return;
        if (rtkNoteTimer) clearTimeout(rtkNoteTimer);
        rtkNoteTimer = null;
        if (text) {
            rtkSetupNote.textContent = text;
            rtkNoteTimer = setTimeout(function() { showRtkNote(''); }, 5000);
        } else {
            rtkSetupNote.textContent = rtkSurvey().label;
        }
    }
    showRtkNote('');
    if (rtkSetupNote) {
        rtkSetupNote.addEventListener('click', function(e) {
            e.stopPropagation();
            rtkSurveyIdx = (rtkSurveyIdx + 1) % RTK_SURVEY_PRESETS.length;
            try { localStorage.setItem('rtkSurveyPreset', String(rtkSurveyIdx)); } catch (err) { /* not remembered */ }
            showRtkNote('');
        });
    }
    if (btnRtkSetup) {
        btnRtkSetup.addEventListener('click', function(e) {
            e.stopPropagation();
            if (!window.SoloViewerRtk || typeof window.SoloViewerRtk.setupBase !== 'function') {
                rtkSetupResult = 'Needs SoloLink Viewer 1.6 or newer (http://10.1.1.10/sololink_viewer.apk).';
            } else {
                let res = '';
                try {
                    res = String(window.SoloViewerRtk.setupBase(rtkSurvey().secs, rtkSurvey().acc));
                } catch (err) {
                    res = err.message;
                }
                rtkSetupResult = res === 'ok' ? 'Settings sent: survey-in started.' : res;
            }
            showRtkNote(rtkSetupResult === 'Settings sent: survey-in started.' ? 'SURVEY STARTED' : rtkSetupResult);
        });
    }
    // RTK base in the browser (no Viewer app): Chrome reads the base over USB itself, with Web Serial
    // on a PC (Windows, Mac, Linux) and WebUSB on Android, and plays the Viewer app's part: the same
    // window.SoloViewerRtk.setupBase, the same status for the RTK lines and the same RTCM frames for
    // window.soloViewerRtcm. Chrome only offers USB on https or localhost pages; for http://10.1.1.10
    // it has to be allowed once in chrome://flags (Insecure origins treated as secure).
    const RTK_UBLOX_VID = 0x1546;
    const rtkWeb = {
        mode: '',                           // 'serial' or 'usb' while a base is open
        port: null, reader: null, writer: null,
        dev: null, epIn: 0, epOut: 0,
        name: '', bytes: 0, nmea: false, ubx: false, svin: null, acks: 0, naks: 0, setupSent: false,
        buf: new Uint8Array(0), ubxBuf: new Uint8Array(0), pending: [],
        lastFrame: 0, quiet: false, timer: null, state: ''
    };
    const rtkWebPossible = !window.SoloViewerRtk && /Chrome\//.test(navigator.userAgent || '') &&
        !/; wv\)/.test(navigator.userAgent || '');      // not inside an Android app's WebView
    const rtkWebAndroid = /Android/.test(navigator.userAgent || '');
    function rtkWebApi() {
        // Android: WebUSB (Chrome's Web Serial there is for Bluetooth); a PC: Web Serial (Windows'
        // own USB serial driver holds the receiver, so WebUSB cannot open it).
        if (rtkWebAndroid) return navigator.usb ? 'usb' : (navigator.serial ? 'serial' : '');
        return navigator.serial ? 'serial' : (navigator.usb ? 'usb' : '');
    }
    function rtkWebStatus(state, message) {
        rtkWeb.state = state;
        window.soloViewerRtkStatus({
            state: state, device: rtkWeb.name, message: message || '',
            info: {
                bytes: rtkWeb.bytes, nmea: rtkWeb.nmea, ubx: rtkWeb.ubx, writable: !!rtkWeb.mode,
                setup_sent: rtkWeb.setupSent, acks: rtkWeb.acks, naks: rtkWeb.naks,
                svin: rtkWeb.svin, browser: rtkWeb.mode || 'none'
            }
        });
    }
    function rtkJoin(a, b) {
        const out = new Uint8Array(a.length + b.length);
        out.set(a, 0);
        out.set(b, a.length);
        return out;
    }
    function rtkCrc24q(b, off, count) {
        let crc = 0;
        for (let i = off; i < off + count; i++) {
            crc ^= b[i] << 16;
            for (let k = 0; k < 8; k++) {
                crc <<= 1;
                if (crc & 0x1000000) crc ^= 0x1864CFB;
            }
        }
        return crc & 0xFFFFFF;
    }
    function rtkB64(b) {
        let s = '';
        for (let i = 0; i < b.length; i++) s += String.fromCharCode(b[i]);
        return btoa(s);
    }
    // Whole RTCM3 frames out of the byte stream (0xD3, 10-bit length, message, CRC-24Q), like RtcmFramer.java
    function rtkFrames(data) {
        let b = rtkJoin(rtkWeb.buf, data);
        let start = 0;
        while (b.length - start >= 3) {
            if (b[start] !== 0xD3 || (b[start + 1] & 0xFC) !== 0) { start++; continue; }
            const length = ((b[start + 1] & 0x03) << 8) | b[start + 2];
            const total = length + 6;
            if (b.length - start < total) break;
            const crc = (b[start + total - 3] << 16) | (b[start + total - 2] << 8) | b[start + total - 1];
            if (length > 0 && rtkCrc24q(b, start, total - 3) === crc) {
                rtkWeb.pending.push(rtkB64(b.subarray(start, start + total)));
                rtkWeb.lastFrame = Date.now();
                start += total;
            } else {
                start++;
            }
        }
        b = b.slice(start);
        rtkWeb.buf = b.length > 4096 ? b.slice(b.length - 4096) : b;
        if (rtkWeb.pending.length > RTK_QUEUE_MAX) rtkWeb.pending.splice(0, rtkWeb.pending.length - RTK_QUEUE_MAX);
    }
    // UBX: NAV-SVIN (survey-in progress) and ACK/NAK for the settings, like UbxScanner.java
    function rtkUbx(data) {
        for (let i = 0; i + 1 < data.length; i++) {
            if (data[i] === 0x24 && data[i + 1] === 0x47) { rtkWeb.nmea = true; break; }   // "$G"
        }
        let b = rtkJoin(rtkWeb.ubxBuf, data);
        let start = 0;
        while (b.length - start >= 8) {
            if (b[start] !== 0xB5 || b[start + 1] !== 0x62) { start++; continue; }
            const plen = b[start + 4] | (b[start + 5] << 8);
            if (plen > 1024) { start++; continue; }
            if (b.length - start < plen + 8) break;
            let ca = 0, cb = 0;
            for (let i = start + 2; i < start + 6 + plen; i++) { ca = (ca + b[i]) & 0xFF; cb = (cb + ca) & 0xFF; }
            if (ca !== b[start + 6 + plen] || cb !== b[start + 7 + plen]) { start++; continue; }
            rtkWeb.ubx = true;
            const cls = b[start + 2], id = b[start + 3], p = start + 6;
            const u32 = function(o) { return (b[p + o] | (b[p + o + 1] << 8) | (b[p + o + 2] << 16)) + b[p + o + 3] * 16777216; };
            if (cls === 0x01 && id === 0x3B && plen >= 40) {
                rtkWeb.svin = {
                    dur: u32(8), acc: Math.round(u32(28) / 100) / 100, obs: u32(32),
                    valid: b[p + 36] !== 0, active: b[p + 37] !== 0
                };
            } else if (cls === 0x05 && id === 0x01) {
                rtkWeb.acks++;
            } else if (cls === 0x05 && id === 0x00) {
                rtkWeb.naks++;
            }
            start += plen + 8;
        }
        b = b.slice(start);
        rtkWeb.ubxBuf = b.length > 4096 ? b.slice(b.length - 4096) : b;
    }
    function rtkWebData(data) {
        if (!data || !data.length) return;
        rtkWeb.bytes += data.length;
        rtkUbx(data);
        rtkFrames(data);
    }
    function rtkWebTick() {
        if (!rtkWeb.mode) return;
        const quiet = Date.now() - rtkWeb.lastFrame > 5000;
        rtkWebStatus(quiet ? 'waiting' : 'connected');
        if (rtkWeb.pending.length) {
            const frames = rtkWeb.pending;
            rtkWeb.pending = [];
            window.soloViewerRtcm({ frames: frames });
        }
    }
    // UBX messages for SET UP BASE: the same bytes as UbxScanner.baseSetup (Mission Planner's set-up)
    function rtkUbxMsg(cls, id, payload) {
        const m = new Uint8Array(payload.length + 8);
        m[0] = 0xB5; m[1] = 0x62; m[2] = cls; m[3] = id;
        m[4] = payload.length & 0xFF; m[5] = payload.length >> 8;
        m.set(payload, 6);
        let ca = 0, cb = 0;
        for (let i = 2; i < payload.length + 6; i++) { ca = (ca + m[i]) & 0xFF; cb = (cb + ca) & 0xFF; }
        m[m.length - 2] = ca;
        m[m.length - 1] = cb;
        return m;
    }
    function rtkPut32(p, o, v) {
        p[o] = v & 0xFF; p[o + 1] = (v >>> 8) & 0xFF; p[o + 2] = (v >>> 16) & 0xFF; p[o + 3] = (v >>> 24) & 0xFF;
    }
    function rtkTmode3(mode, secs, accM) {
        const p = new Uint8Array(40);
        p[2] = mode;
        rtkPut32(p, 24, secs);
        rtkPut32(p, 28, Math.round(accM * 10000));
        return rtkUbxMsg(0x06, 0x71, p);
    }
    function rtkBaseSetup(secs, accM) {
        const port = new Uint8Array(20);
        port[0] = 3; port[12] = 0x27; port[14] = 0x23;           // USB: in UBX+NMEA+RTCM, out UBX+NMEA+RTCM3
        const rate = function(cls, id) { return rtkUbxMsg(0x06, 0x01, new Uint8Array([cls, id, 1])); };
        return [rtkUbxMsg(0x06, 0x00, port),
            rate(0xF5, 0x05), rate(0xF5, 0x4D), rate(0xF5, 0x57), rate(0xF5, 0x61), rate(0xF5, 0x7F), rate(0xF5, 0xE6),
            rate(0x01, 0x3B), rtkTmode3(0, 0, 0), rtkTmode3(1, secs, accM)];
    }
    function rtkWebWrite(bytes) {
        if (rtkWeb.mode === 'serial' && rtkWeb.writer) return rtkWeb.writer.write(bytes);
        if (rtkWeb.mode === 'usb' && rtkWeb.dev) return rtkWeb.dev.transferOut(rtkWeb.epOut, bytes);
        return Promise.reject(new Error('No base receiver is connected.'));
    }
    async function rtkWebSetup(secs, accM) {
        const msgs = rtkBaseSetup(secs, accM);
        for (let i = 0; i < msgs.length; i++) {
            await rtkWebWrite(msgs[i]);
            await new Promise(function(r) { setTimeout(r, 30); });
        }
        rtkWeb.setupSent = true;
    }
    function rtkWebReset() {
        rtkWeb.bytes = 0; rtkWeb.nmea = false; rtkWeb.ubx = false; rtkWeb.svin = null;
        rtkWeb.acks = 0; rtkWeb.naks = 0; rtkWeb.setupSent = false;
        rtkWeb.buf = new Uint8Array(0); rtkWeb.ubxBuf = new Uint8Array(0); rtkWeb.pending = [];
        rtkWeb.lastFrame = Date.now();
    }
    function rtkWebStarted(name) {
        rtkWeb.name = name;
        rtkWebStatus('connected');
        if (rtkWeb.timer) clearInterval(rtkWeb.timer);
        rtkWeb.timer = setInterval(rtkWebTick, 1000);
    }
    function rtkWebClosed(message) {
        rtkWeb.mode = '';
        rtkWeb.reader = null; rtkWeb.writer = null; rtkWeb.port = null; rtkWeb.dev = null;
        if (rtkWeb.timer) clearInterval(rtkWeb.timer);
        rtkWeb.timer = null;
        rtkWebStatus(message ? 'error' : 'none', message || 'No base receiver on this computer\'s USB port. Tap USB BASE.');
    }
    async function rtkOpenSerial(port) {
        if (rtkWeb.mode) return;
        await port.open({ baudRate: 115200 });          // a u-blox USB port ignores the speed
        try { await port.setSignals({ dataTerminalReady: true, requestToSend: true }); } catch (e) { /* optional */ }
        rtkWebReset();
        rtkWeb.mode = 'serial';
        rtkWeb.port = port;
        rtkWeb.writer = port.writable.getWriter();
        const vid = (port.getInfo && port.getInfo().usbVendorId) || 0;
        rtkWebStarted(vid === RTK_UBLOX_VID ? 'u-blox receiver' : 'USB serial receiver');
        (async function() {
            let failed = '';
            try {
                while (rtkWeb.port === port && port.readable) {
                    rtkWeb.reader = port.readable.getReader();
                    try {
                        for (;;) {
                            const r = await rtkWeb.reader.read();
                            if (r.done) break;
                            rtkWebData(r.value);
                        }
                    } finally {
                        try { rtkWeb.reader.releaseLock(); } catch (e) { /* closed */ }
                    }
                }
            } catch (e) {
                failed = (e && e.name === 'NetworkError') ? '' : ('Reading the base failed: ' + e.message);
            }
            try { if (rtkWeb.writer) rtkWeb.writer.releaseLock(); } catch (e) { /* closed */ }
            try { await port.close(); } catch (e) { /* unplugged */ }
            if (rtkWeb.port === port) rtkWebClosed(failed);
        })();
    }
    async function rtkOpenUsb(dev) {
        if (rtkWeb.mode) return;
        await dev.open();
        if (!dev.configuration) await dev.selectConfiguration(1);
        let data = null, ctrl = null, epIn = 0, epOut = 0;
        dev.configuration.interfaces.forEach(function(itf) {
            const alt = itf.alternate || itf.alternates[0];
            if (alt.interfaceClass === 2 && !ctrl) ctrl = itf;
            if (alt.interfaceClass === 10 && !data) {
                alt.endpoints.forEach(function(ep) {
                    if (ep.type !== 'bulk') return;
                    if (ep.direction === 'in') epIn = ep.endpointNumber; else epOut = ep.endpointNumber;
                });
                if (epIn) data = itf;
            }
        });
        if (!data) { try { await dev.close(); } catch (e) { /* ignore */ } throw new Error('Not a USB serial (CDC) receiver.'); }
        await dev.claimInterface(data.interfaceNumber);
        if (ctrl) {
            try {
                await dev.claimInterface(ctrl.interfaceNumber);
                const idx = ctrl.interfaceNumber;
                await dev.controlTransferOut({ requestType: 'class', recipient: 'interface', request: 0x22, value: 3, index: idx });
                await dev.controlTransferOut({ requestType: 'class', recipient: 'interface', request: 0x20, value: 0, index: idx },
                    new Uint8Array([0x00, 0xC2, 0x01, 0x00, 0x00, 0x00, 0x08]));     // DTR+RTS, 115200 8N1
            } catch (e) { /* some receivers send without it */ }
        }
        rtkWebReset();
        rtkWeb.mode = 'usb';
        rtkWeb.dev = dev; rtkWeb.epIn = epIn; rtkWeb.epOut = epOut;
        rtkWebStarted(dev.vendorId === RTK_UBLOX_VID ? 'u-blox receiver' : (dev.productName || 'USB receiver'));
        (async function() {
            let failed = '';
            try {
                while (rtkWeb.dev === dev) {
                    const r = await dev.transferIn(epIn, 16384);
                    if (r.data && r.data.byteLength) rtkWebData(new Uint8Array(r.data.buffer, r.data.byteOffset, r.data.byteLength));
                }
            } catch (e) {
                failed = (e && e.name === 'NotFoundError') ? '' : ('Reading the base failed: ' + e.message);
            }
            try { await dev.close(); } catch (e) { /* unplugged */ }
            if (rtkWeb.dev === dev) rtkWebClosed(failed);
        })();
    }
    // The USB BASE button: Chrome asks which receiver to use (once; after that it reconnects by itself)
    async function rtkWebConnect() {
        if (!window.isSecureContext) {
            rtkWebStatus('blocked', 'Chrome only allows USB on secure pages. Open chrome://flags, search for ' +
                '"Insecure origins treated as secure", add http://10.1.1.10, set it to Enabled and relaunch Chrome.');
            return;
        }
        const api = rtkWebApi();
        if (!api) {
            rtkWebStatus('error', 'This browser cannot use USB devices. Use Chrome or Edge on a PC, Chrome on Android, or the SoloLink Viewer app.');
            return;
        }
        try {
            if (api === 'serial') {
                const port = await navigator.serial.requestPort();
                await rtkOpenSerial(port);
            } else {
                const dev = await navigator.usb.requestDevice({ filters: [{ vendorId: RTK_UBLOX_VID }, { classCode: 2 }] });
                await rtkOpenUsb(dev);
            }
        } catch (e) {
            if (e && e.name === 'NotFoundError') return;          // the picker was closed
            rtkWebClosed('Could not open the base: ' + e.message + (api === 'serial' ? ' (close Mission Planner or u-center if they have it open)' : ''));
        }
    }
    // Reconnect a receiver Chrome already allowed, now and whenever it is plugged in
    async function rtkWebAuto() {
        if (!rtkWebPossible || !window.isSecureContext || rtkWeb.mode) return;
        const api = rtkWebApi();
        try {
            if (api === 'serial') {
                const ports = await navigator.serial.getPorts();
                const port = ports.filter(function(p) { return p.getInfo && p.getInfo().usbVendorId === RTK_UBLOX_VID; })[0] || ports[0];
                if (port) await rtkOpenSerial(port);
            } else if (api === 'usb') {
                const devs = await navigator.usb.getDevices();
                if (devs.length) await rtkOpenUsb(devs[0]);
            }
        } catch (e) { /* the button can still be used */ }
    }
    if (rtkWebPossible) {
        window.SoloViewerRtk = {
            browser: true,
            setupBase: function(secs, accM) {
                if (!rtkWeb.mode) return 'No base receiver is connected.';
                rtkWebSetup(secs, accM).catch(function(e) {
                    showRtkNote('SETUP FAILED');
                    rtkWebStatus('error', 'Sending the settings to the base failed: ' + e.message);
                });
                return 'ok';
            }
        };
        if (window.isSecureContext && navigator.serial && navigator.serial.addEventListener) {
            navigator.serial.addEventListener('connect', function() { setTimeout(rtkWebAuto, 500); });
        }
        if (window.isSecureContext && navigator.usb && navigator.usb.addEventListener) {
            navigator.usb.addEventListener('connect', function() { setTimeout(rtkWebAuto, 500); });
        }
        setTimeout(rtkWebAuto, 1000);
    }
    if (btnRtkUsb) {
        btnRtkUsb.addEventListener('click', function(e) {
            e.stopPropagation();
            rtkWebConnect();
        });
    }

    function updateRtk(telem) {
        if (!rtkRow) return;
        const rtk = telem.rtk || {};
        const app = (rtkApp && rtkApp.state) ? rtkApp : (rtk.app || {});
        const info = app.info || {};
        const svin = info.svin || null;
        const used = rtk.frames > 0 || !!app.state;
        rtkRow.style.display = used ? '' : 'none';
        if (rtkSetupRow) {
            const canSetup = !!window.SoloViewerRtk && (app.state === 'connected' || app.state === 'waiting');
            // In Chrome without the Viewer app: a USB BASE button to pick the receiver (until one is open)
            const offerUsb = rtkWebPossible && !rtkWeb.mode;
            rtkSetupRow.style.display = (canSetup || offerUsb) ? '' : 'none';
            if (btnRtkUsb) btnRtkUsb.style.display = offerUsb ? '' : 'none';
            if (btnRtkSetup) btnRtkSetup.style.display = canSetup ? '' : 'none';
            if (rtkSetupNote) rtkSetupNote.style.display = canSetup ? '' : 'none';
            if (btnRtkSetup) btnRtkSetup.textContent = (svin && svin.active) ? 'RESTART SURVEY' : 'SET UP BASE';
        }
        // Second line, readable on a phone (tooltips never show there): which RTCM messages reach
        // the Cube and whether the base has sent its own position (1005/1006), without which the
        // Solo's GPS cannot use the corrections at all.
        if (rtkMsgsRow) {
            // In the browser, why USB does not work goes here (a tooltip never shows on a phone)
            const webHint = rtkWebPossible && (app.state === 'blocked' || app.state === 'error') ? (app.message || '') : '';
            const showMsgs = used && (rtk.active || (rtk.types || []).length > 0 || !!webHint);
            rtkMsgsRow.style.display = showMsgs ? '' : 'none';
            if (showMsgs && rtkMsgsVal && webHint && !rtk.active) {
                rtkMsgsVal.textContent = webHint;
            } else if (showMsgs && rtkMsgsVal) {
                const types = rtk.types || [];
                rtkMsgsVal.textContent = (types.length ? types.join(' ') : 'NONE') +
                    ' · ' + ((rtk.rate_bps || 0) / 1024).toFixed(1) + ' KB/s' +
                    (rtk.rejected ? ' · ' + rtk.rejected + ' BAD' : '');
            }
        }
        if (!used) return;
        let text, color, title;
        if (svin && svin.active) {
            // Survey-in still running: show it even while some RTCM already flows (the base does
            // not send its position until the survey is done, so the Solo cannot use RTK yet).
            // What matters while surveying: is the base position valid yet, and how close is it
            // (the current accuracy must shrink below the target before the survey can finish).
            text = (svin.valid ? 'VALID' : 'INVALID') + ' · ' + Number(svin.acc).toFixed(1) + ' m · ' + svin.dur + ' s';
            color = svin.valid ? 'var(--accent-emerald)' : 'var(--accent-amber)';
            title = 'The base is measuring its own position (survey-in): it needs at least ' + rtkSurvey().secs +
                ' s and ' + rtkSurvey().acc + ' m. Corrections start when it is done. Keep the base still with a clear view of the sky.';
        } else if (rtk.active) {
            // The GPS pill is hidden in the full-screen HUD, so the fix type goes on this line:
            // what the Solo's GPS makes of the corrections (6 RTK fixed, 5 RTK float, 4 DGPS).
            const gps = telem.gps || {};
            const fixType = (typeof gps.fix_type === 'number') ? gps.fix_type :
                ((typeof telem.gps_fix_type === 'number') ? telem.gps_fix_type : 0);
            const fixWord = fixType === 6 ? 'FIX' : fixType === 5 ? 'FLOAT' : fixType === 4 ? 'DGPS' :
                (fixType >= 3 ? '3D' : 'NO FIX');
            // Lead with the base position (valid or not, and its survey accuracy), then the
            // Solo's fix type; the data rate is on the RTK Msgs line below.
            let posWord;
            if (svin && svin.valid) posWord = 'VALID ' + Number(svin.acc).toFixed(1) + ' m';
            else if (svin) posWord = 'INVALID ' + Number(svin.acc).toFixed(1) + ' m';
            else posWord = rtk.base ? 'POS OK' : 'NO POS';
            const posOk = svin ? !!svin.valid : !!rtk.base;
            text = posWord + ' · ' + fixWord;
            color = fixType === 6 ? 'var(--accent-emerald)' : (fixType >= 4 ? 'var(--accent-amber)' :
                (posOk ? '#e2e8f0' : 'var(--accent-amber)'));
            title = (fixType === 6 ? 'RTK fixed: about 1-2 cm against the base. ' :
                fixType === 5 ? 'RTK float: about 0.2-1 m; it turns into FIX when the GPS solves the carrier phase. ' :
                fixType === 4 ? 'DGPS: the GPS uses the corrections, but not RTK yet. ' :
                'The GPS has corrections but no RTK solution yet (needs an RTK-capable GPS, about 12+ satellites and open sky). ') +
                'Last correction ' + (rtk.age_s || 0).toFixed(1) + ' s ago. ' +
                'RTCM corrections reach the Cube (' + (rtk.types || []).join(', ') + ').';
            if (rtk.base) title += ' Base at ' + rtk.base.lat.toFixed(7) + ', ' + rtk.base.lon.toFixed(7) +
                ', ' + rtk.base.alt.toFixed(1) + ' m.';
            else title += ' The base has not sent its position (1005) yet.';
        } else if (RTK_APP_TEXT[app.state]) {
            text = RTK_APP_TEXT[app.state][0];
            title = app.message || RTK_APP_TEXT[app.state][1];
            if (app.state === 'waiting') {
                // what the base does send: nothing at all, or GPS text / UBX without corrections
                const kinds = [];
                if (info.nmea) kinds.push('NMEA');
                if (info.ubx) kinds.push('UBX');
                title += ' The base sent ' + (info.bytes || 0) + ' bytes' + (kinds.length ? ' (' + kinds.join(', ') + ')' : '') + '.';
                if (svin && svin.valid && !svin.active) title += ' Survey-in is done but RTCM output is off.';
                if (info.setup_sent && info.naks) title += ' The base refused ' + info.naks + ' setting(s) it does not have.';
            }
            color = app.state === 'none' ? 'var(--text-secondary)' : 'var(--accent-amber)';
        } else if (typeof rtk.age_s === 'number') {
            text = 'NO DATA ' + Math.round(rtk.age_s) + ' s';
            title = 'No corrections for ' + Math.round(rtk.age_s) + ' s. The Solo falls back from RTK to a normal GPS fix.';
            color = 'var(--accent-red)';
        } else {
            text = 'WAITING';
            title = 'Waiting for corrections from the base.';
            color = 'var(--accent-amber)';
        }
        const problem = rtkSendProblem || rtk.last_error || '';
        if (problem && !rtk.active) title += ' (' + problem + ')';
        rtkBaseVal.textContent = text;
        rtkBaseVal.style.color = color;
        rtkRow.title = title;
    }

    function setFollowStyleLabel(style) {
        if (!btnFollowStyle || !FOLLOW_STYLE_LABELS[style]) return;
        btnFollowStyle.dataset.style = style;
        btnFollowStyle.textContent = 'FOLLOW: ' + FOLLOW_STYLE_LABELS[style];
    }

    if (btnFollowStyle) {
        btnFollowStyle.addEventListener('click', function(e) {
            e.stopPropagation();
            const i = FOLLOW_STYLES.indexOf(btnFollowStyle.dataset.style);
            const next = FOLLOW_STYLES[(i + 1) % FOLLOW_STYLES.length];
            setFollowStyleLabel(next);
            postAction({ action: 'follow_style', mode: next }).then(function(data) {
                const ok = data && data.status === 'ok';
                setSmartShotMsg((data && data.message) || 'Could not change the follow style.', !ok);
            }).catch(function(err) {
                setSmartShotMsg('Could not change the follow style: ' + err.message, true);
            });
        });
    }

    // Selfie settings (kept by the server, shown here): how far away, how high, how fast
    const btnSelfieSettings = document.getElementById('btnSelfieSettings');
    const selfieSettings = document.getElementById('selfieSettings');
    const selfieInputs = {
        distance: document.getElementById('selfieDistanceIn'),
        rise: document.getElementById('selfieRiseIn'),
        speed: document.getElementById('selfieSpeedIn')
    };
    let lastShotDetail = '';

    if (btnSelfieSettings && selfieSettings) {
        btnSelfieSettings.addEventListener('click', function(e) {
            e.stopPropagation();
            selfieSettings.hidden = !selfieSettings.hidden;
            btnSelfieSettings.setAttribute('aria-expanded', selfieSettings.hidden ? 'false' : 'true');
            layoutShotRow();
            layoutOsdBar();
        });
        selfieSettings.addEventListener('click', function(e) { e.stopPropagation(); });
    }
    Object.keys(selfieInputs).forEach(function(key) {
        const input = selfieInputs[key];
        if (!input) return;
        input.addEventListener('change', function() {
            const v = parseFloat(input.value);
            if (!isFinite(v)) return;
            const body = {};
            body[key] = v;
            postAction({ action: 'selfie_settings', mode: body }).then(function(data) {
                const ok = data && data.status === 'ok';
                setSmartShotMsg((data && data.message) || 'Could not change the Selfie settings.', !ok);
            }).catch(function(err) {
                setSmartShotMsg('Could not change the Selfie settings: ' + err.message, true);
            });
        });
    });

    function updateSelfieSettings(telem) {
        const s = (telem.smart_shot || {}).selfie;
        if (!s) return;
        Object.keys(selfieInputs).forEach(function(key) {
            const input = selfieInputs[key];
            if (input && document.activeElement !== input && typeof s[key] === 'number') input.value = s[key];
        });
    }

    // FOLLOW is usable only while the server has a good position from this phone
    function updateFollow(telem) {
        const ss = telem.smart_shot || {};
        if (ss.follow_style) setFollowStyleLabel(ss.follow_style);
        if (!btnFollow) return;
        const pilot = telem.pilot || {};
        const ready = !!pilot.ready;
        btnFollow.classList.toggle('needs-app', !ready);
        if (ready) {
            delete btnFollow.dataset.needs;
            btnFollow.title = 'Follow you (phone GPS good to ' + pilot.accuracy + ' m)';
        } else {
            // the server only knows it got nothing: say why, if the phone can tell
            let problem = pilot.problem;
            if (VIEWER_GPS_PROBLEMS[viewerGpsStatus]) {
                problem = VIEWER_GPS_PROBLEMS[viewerGpsStatus];
            } else if (typeof pilot.age !== 'number') {
                if (pilotFixRejected) problem = pilotFixRejected;
                else if (!viewerGpsStatus && inAndroidWebView) {
                    problem = 'this SoloLink Viewer app does not send the phone\'s GPS. Install version 1.4 or ' +
                        'newer: open http://10.1.1.10/sololink_viewer.apk in the phone\'s browser';
                }
            } else if (pilotFixRejected) {
                problem += ' (' + pilotFixRejected + ')';
            }
            btnFollow.dataset.needs = problem ? 'Follow cannot start: ' + problem + '.' : followNeedsDefault;
            btnFollow.title = btnFollow.dataset.needs;
        }
    }

    // Telemetry carries the shot the utility started (-1: none)
    function updateSmartShots(telem) {
        updateFollow(telem);
        updateSelfieSettings(telem);
        const ss = telem.smart_shot || {};
        const shot = (typeof ss.shot === 'number') ? ss.shot : -1;
        // what the running shot is doing or waiting for (e.g. Multipoint's recorded points)
        if (ss.detail && ss.detail !== lastShotDetail) setSmartShotMsg(ss.detail, false);
        lastShotDetail = ss.detail || '';
        if (shot === lastSmartShot) return;
        if (lastSmartShot !== null && lastSmartShot !== -1 && shot === -1) {
            setSmartShotMsg('Smart Shot ended.', false);
            speak('Smart shot ended');
        }
        lastSmartShot = shot;
        shotButtons.forEach(function(b) {
            b.classList.toggle('running', parseInt(b.dataset.shot, 10) === shot);
        });
        if (btnEndShot) btnEndShot.disabled = shot === -1;
        if (btnSmartShots) {
            btnSmartShots.classList.toggle('shot-running', shot !== -1);
            btnSmartShots.dataset.shotName = shot !== -1 ? (ss.name || '') : '';
            setSmartShotLabel(btnSmartShots.dataset.shotName);
        }
    }

    // =========================================================================
    // Precision Landing: the pad finder (padfinder.js) looks for the printed landing pad in the
    // live video ~10 times a second and the server passes every sighting on to the Cube as
    // LANDING_TARGET. ArduPilot's own precision landing then lands on the pad in LAND, RTL and
    // Smart RTL, and Precision Loiter holds over it in Fly. While the pad is not seen nothing is
    // sent, and the Solo lands or loiters as it normally would.
    // =========================================================================
    const btnPrecision = document.getElementById('btnPrecision');
    const precisionRow = document.getElementById('precisionRow');
    const btnPadFinder = document.getElementById('btnPadFinder');
    const btnCameraDown = document.getElementById('btnCameraDown');
    const btnPrecLoiter = document.getElementById('btnPrecLoiter');
    const btnPrecLand = document.getElementById('btnPrecLand');
    const btnPadSettings = document.getElementById('btnPadSettings');
    const padSettings = document.getElementById('padSettings');
    const precisionMsg = document.getElementById('precisionMsg');
    const padSizeIn = document.getElementById('padSizeIn');
    const padSizeUnit = document.getElementById('padSizeUnit');
    const padFovIn = document.getElementById('padFovIn');
    const padLensIn = document.getElementById('padLensIn');
    const padDelayIn = document.getElementById('padDelayIn');
    const padOverlay = document.getElementById('padOverlay');
    const padOverlayCtx = padOverlay ? padOverlay.getContext('2d') : null;

    const PAD_TICK_MS = 80;              // look at most ~12 times a second (new video frames only)
    const PAD_WORK_WIDTH_MAX = 960;      // pixels across the frame the pad finder works on
    const PAD_WORK_WIDTH_MIN = 640;      // ... when a look takes too long on this phone
    const PAD_SLOW_MS = 70;
    const PAD_VIDEO_FRESH_MS = 1000;     // no video data for longer: the frame on screen is old, not used
    const PAD_NO_VIDEO_MSG_MS = 2000;    // ... and only after this long without usable video say so
    const PAD_LOST_MS = 1500;
    const PAD_CHECK_EVERY_MS = 60000;    // re-read the Cube's PLND_ settings at most this often
    const PAD_LAND_MODES = ['LAND', 'RTL', 'SMART_RTL'];

    function loadPadSetting(key, fallback) {
        try {
            const v = localStorage.getItem(key);
            if (v !== null && v !== '') return v;
        } catch (e) {}
        return fallback;
    }
    function savePadSetting(key, value) {
        try { localStorage.setItem(key, String(value)); } catch (e) {}
    }
    let padSizeM = parseFloat(loadPadSetting('solo_pad_size_m', '0.6096')) || 0.6096;   // 24 in black square
    let padFovDeg = parseFloat(loadPadSetting('solo_pad_fov_deg', '118')) || 118;       // GoPro Wide
    let padLens = loadPadSetting('solo_pad_lens', 'fisheye') === 'rectilinear' ? 'rectilinear' : 'fisheye';
    // How far behind the GoPro the video on this screen is (s): measured with a stopwatch.
    // The server moves each sighting on by what the Cube's PLND_LAG doesn't cover.
    let padDelayS = parseFloat(loadPadSetting('solo_video_delay_s', '0.4'));
    if (!(padDelayS >= 0 && padDelayS <= 1.5)) padDelayS = 0.4;

    let padFinderOn = false;
    let padWorkWidth = PAD_WORK_WIDTH_MAX;
    let padSlowMs = 0;                   // smoothed time one look takes
    let padLastFrameTime = -1;
    let padNoVideoSince = 0;             // when the video stopped being usable (0: it is)
    let padPostBusy = false;
    let padLastSeen = 0;
    let padCheck = null;                 // last /api/precland/check answer: {ready, message}
    let padCheckTime = 0;
    let padCheckBusy = false;
    let padLastMode = null;
    let padServer = {};                  // telemetry.precland
    let padMsgKey = '';
    let padGray = null;
    const padCanvas = document.createElement('canvas');
    const padCtx = padCanvas.getContext('2d', { willReadFrequently: true });

    // Takeoff spot (spotfinder.js): with TAKEOFF SPOT on, arming turns the camera down and the climb
    // saves pictures of the ground around the takeoff spot; the camera goes back to the paddle by
    // 10 m. On the way back down the live video is matched against them and the spot goes to the
    // Cube like a pad sighting. A seen pad always wins.
    const btnTakeoffSpot = document.getElementById('btnTakeoffSpot');
    const SPOT_HEIGHTS = [2, 4, 8];      // pictures on the climb (m above the spot)
    const SPOT_HEIGHT_WINDOW = 1.5;      // ... tried up to 1.5 x that height, then skipped
    const SPOT_HANDBACK_ALT = 10;        // the camera is back on the paddle by this height whatever happened
    const SPOT_AWAY_M = 3;               // ... or once the Solo is this far sideways (the spot leaves the view)
    const SPOT_GIMBAL_MS = 1500;         // time for the gimbal to turn down after arming
    const SPOT_CAMERA_HEIGHT = 0.15;     // the GoPro above the ground with the Solo on its legs (m)
    const SPOT_VIDEO_LAG_S = 0.25;       // the video runs about this far behind the telemetry
    const SPOT_SEARCH_M = 6;             // how far from where GPS puts it the spot is looked for
    const SPOT_TRACK_M = 1.5;            // ... once it has been found (+5% of the height)
    const SPOT_AGREE_M = 0.6;            // matches in a row must agree this well (+5% of the height)
    const SPOT_AGREE_COUNT = 3;          // ... this many before anything goes to the Cube
    const SPOT_STEADY_MS = 1000;         // ... and the last 3 within this (the Cube wants one every 0.5 s)
    const SPOT_LOG_MS = 1000;            // the way down: one summary line a second in the Solo's log
    const SPOT_NEAR_M = 15;              // only look when GPS has the Solo this close (+ its height)
    let spotOn = false;
    let spotRefs = [];                   // SpotFinder references from the last climb
    let spotHome = null;                 // [lat, lon] where the Solo was armed
    let spotGround = [];                 // the last GPS positions while disarmed
    let spotNext = 0;                    // index into SPOT_HEIGHTS of the next picture
    let spotCapturing = false;           // the camera is down for the climb pictures
    let spotDownTime = 0;
    let spotNotes = [];                  // why a height was skipped
    let spotLastReason = '';             // why the last try at the next height failed
    let spotTrack = null;                // SpotFinder.track(): {dn, de: the spot vs GPS (m), count, locked, ...}
    let spotStats = null;                // the way down: what the last second's looks found (for the log)
    let spotLastArmed = null;
    let spotLastSeen = 0;
    let spotMsgUntil = 0;                // the end-of-climb message stays up until then
    const SPOT_MSG_HOLD_MS = 5000;

    function setPrecisionMsg(text, isError) {
        if (!precisionMsg) return;
        const key = (isError ? '!' : '') + text;
        if (key === padMsgKey) return;
        padMsgKey = key;
        precisionMsg.textContent = text;
        precisionMsg.classList.toggle('error', !!isError);
        layoutShotRow();
    }

    function setPrecisionLabel() {
        if (!btnPrecision || !precisionRow) return;
        const on = padFinderOn || spotOn;
        btnPrecision.textContent = '🎯 ' + (on ? 'PRECISION ON' : 'PRECISION') + (precisionRow.hidden ? ' ▾' : ' ▴');
        btnPrecision.classList.toggle('finder-on', on);
    }

    function closePrecisionRow() {
        if (!precisionRow || precisionRow.hidden) return;
        precisionRow.hidden = true;
        if (btnPrecision) btnPrecision.setAttribute('aria-expanded', 'false');
        setPrecisionLabel();
    }

    function showPadSettings() {
        const imperial = unitSystem === 'imperial';
        if (padSizeIn) padSizeIn.value = imperial ? (padSizeM / 0.0254).toFixed(1) : (padSizeM * 100).toFixed(1);
        if (padSizeUnit) padSizeUnit.textContent = imperial ? 'in' : 'cm';
        if (padFovIn) padFovIn.value = Math.round(padFovDeg);
        if (padLensIn) padLensIn.value = padLens;
        if (padDelayIn) padDelayIn.value = padDelayS.toFixed(2);
    }

    // Is the Cube set up (PLND_ENABLED 1, PLND_TYPE 1)? Without it the sightings do nothing.
    function checkPrecisionSetup(force) {
        if (padCheckBusy || (!force && Date.now() - padCheckTime < PAD_CHECK_EVERY_MS)) return;
        padCheckBusy = true;
        fetch('/api/precland/check', { cache: 'no-store', headers: authHeaders() })
            .then(function(r) {
                if (r.status === 401) { authRequired(); throw new Error('Login required'); }
                return r.json();
            })
            .then(function(res) {
                if (!res || typeof res.message !== 'string') return;
                padCheck = res;
                padCheckTime = Date.now();
                if (!padFinderOn || !res.ready) setPrecisionMsg(res.message, !res.ready);
            })
            .catch(function() {})
            .then(function() { padCheckBusy = false; });
    }

    if (btnPrecision && precisionRow) {
        btnPrecision.addEventListener('click', function(e) {
            e.stopPropagation();
            const open = precisionRow.hidden;
            precisionRow.hidden = !open;
            btnPrecision.setAttribute('aria-expanded', open ? 'true' : 'false');
            if (open && smartShotRow && !smartShotRow.hidden) {
                smartShotRow.hidden = true;
                if (btnSmartShots) btnSmartShots.setAttribute('aria-expanded', 'false');
                setSmartShotLabel(btnSmartShots && btnSmartShots.classList.contains('shot-running') ? btnSmartShots.dataset.shotName : '');
            }
            setPrecisionLabel();
            if (open) {
                showPadSettings();
                checkPrecisionSetup(false);
            }
            layoutShotRow();
            layoutOsdBar();
        });
    }

    if (btnPadSettings && padSettings) {
        btnPadSettings.addEventListener('click', function(e) {
            e.stopPropagation();
            padSettings.hidden = !padSettings.hidden;
            btnPadSettings.setAttribute('aria-expanded', padSettings.hidden ? 'false' : 'true');
            showPadSettings();
            layoutShotRow();
            layoutOsdBar();
        });
        padSettings.addEventListener('click', function(e) { e.stopPropagation(); });
    }
    if (padSizeIn) {
        padSizeIn.addEventListener('change', function() {
            const v = parseFloat(padSizeIn.value);
            if (v > 0) {
                const m = unitSystem === 'imperial' ? v * 0.0254 : v / 100;
                padSizeM = Math.min(4, Math.max(0.1, m));
                savePadSetting('solo_pad_size_m', padSizeM);
            }
            showPadSettings();
        });
    }
    if (padFovIn) {
        padFovIn.addEventListener('change', function() {
            const v = parseFloat(padFovIn.value);
            if (v > 0) {
                padFovDeg = Math.min(170, Math.max(40, v));
                savePadSetting('solo_pad_fov_deg', padFovDeg);
            }
            showPadSettings();
        });
    }
    if (padLensIn) {
        padLensIn.addEventListener('change', function() {
            padLens = padLensIn.value === 'rectilinear' ? 'rectilinear' : 'fisheye';
            savePadSetting('solo_pad_lens', padLens);
        });
    }
    if (padDelayIn) {
        padDelayIn.addEventListener('change', function() {
            const v = parseFloat(padDelayIn.value);
            if (v >= 0) {
                padDelayS = Math.min(1.5, v);
                savePadSetting('solo_video_delay_s', padDelayS);
            }
            showPadSettings();
        });
    }

    if (btnPadFinder) {
        btnPadFinder.addEventListener('click', function(e) {
            e.stopPropagation();
            padFinderOn = !padFinderOn;
            btnPadFinder.classList.toggle('active', padFinderOn);
            btnPadFinder.setAttribute('aria-pressed', padFinderOn ? 'true' : 'false');
            btnPadFinder.textContent = '🎯 PAD FINDER: ' + (padFinderOn ? 'ON' : 'OFF');
            setPrecisionLabel();
            padLastFrameTime = -1;
            if (padFinderOn) {
                setPrecisionMsg('Looking for the pad. Point the camera down and fly over it.', false);
                speak('Pad finder on');
                checkPrecisionSetup(false);
            } else {
                clearPadOverlay();
                setPrecisionMsg('Pad finder off: nothing is sent to the Cube.', false);
                speak('Pad finder off');
            }
        });
    }

    function telemArmed(t) {
        return !!(t && t.status && t.status.armed);
    }

    // The Solo's offset from the takeoff spot, metres north / east (GPS)
    function spotOffset(t) {
        if (!spotHome || !t || !(t.gps_fix_type >= 3) || !(t.lat || t.lon)) return null;
        const k = 111320;
        return { n: (t.lat - spotHome[0]) * k, e: (t.lon - spotHome[1]) * k * Math.cos(spotHome[0] * Math.PI / 180) };
    }

    // Height above the takeoff spot and heading when the frame now on screen was taken
    function spotView(t) {
        const alt = (t.alt_rel || 0) - (t.climb_rate || 0) * SPOT_VIDEO_LAG_S;
        return { height: Math.max(0.3, alt + SPOT_CAMERA_HEIGHT), yaw: t.yaw || 0 };
    }

    function setSpotButton() {
        if (!btnTakeoffSpot) return;
        btnTakeoffSpot.classList.toggle('active', spotOn);
        btnTakeoffSpot.setAttribute('aria-pressed', spotOn ? 'true' : 'false');
        btnTakeoffSpot.textContent = '📍 TAKEOFF SPOT: ' + (spotOn ? (spotRefs.length ? 'ON · ' + spotRefs.length + '/3' : 'ON') : 'OFF');
    }

    // Armed with TAKEOFF SPOT on: remember where, turn the camera down for the climb pictures
    function spotStartClimb(t) {
        spotRefs = [];
        spotNext = 0;
        spotNotes = [];
        spotLastReason = '';
        spotTrack = null;
        spotStats = null;
        let pts = spotGround.slice();
        if (!pts.length && t && t.gps_fix_type >= 3 && (t.lat || t.lon)) pts = [[t.lat, t.lon]];
        if (!pts.length) {
            spotHome = null;
            setSpotButton();
            setPrecisionMsg('TAKEOFF SPOT: no GPS fix when armed, so the spot can\'t be saved. The landing uses GPS.', true);
            spotLog('TAKEOFF SPOT: no GPS fix when armed, nothing saved', true);
            return;
        }
        spotHome = [0, 1].map(function(i) {
            return pts.reduce(function(s, p) { return s + p[i]; }, 0) / pts.length;
        });
        spotCapturing = true;
        spotDownTime = Date.now();
        postAction({ action: 'camera_down' }).catch(function() {});
        setSpotButton();
        setPrecisionMsg('TAKEOFF SPOT: camera down. Pictures at 2, 4 and 8 m on the climb; the camera goes back to the paddle by 10 m.', false);
        speak('Camera down for the takeoff spot');
        spotLog('TAKEOFF SPOT: armed, camera down for the climb pictures (2, 4, 8 m)', true);
    }

    // Done with the climb pictures (or gave up): the camera goes back to the paddle
    function spotFinishClimb(reason) {
        if (!spotCapturing) return;
        spotCapturing = false;
        postAction({ action: 'camera_paddle' }).catch(function() {});
        const n = spotRefs.length;
        setSpotButton();
        spotMsgUntil = Date.now() + SPOT_MSG_HOLD_MS;
        if (n) {
            setPrecisionMsg('TAKEOFF SPOT: ' + n + ' of 3 pictures saved' + (reason ? ' (' + reason + ')' : '') +
                            '. Camera back on the paddle. LAND, RTL and Smart RTL land on the spot.', false);
            speak('Takeoff spot saved');
        } else {
            const why = spotNotes.length ? spotNotes.join('; ') : reason;
            setPrecisionMsg('TAKEOFF SPOT: no pictures saved' + (why ? ' (' + why + ')' : '') +
                            '. The landing uses GPS. Camera back on the paddle.', true);
            speak('Takeoff spot not saved');
        }
        spotLog('TAKEOFF SPOT: climb done, ' + n + ' of 3 pictures saved' + (reason ? ' (' + reason + ')' : '') +
                (spotNotes.length ? '; skipped: ' + spotNotes.join('; ') : '') + '. Camera back on the paddle', true);
    }

    // Telemetry side of the takeoff spot: arming, and when to stop the climb pictures
    function updateTakeoffSpot(t, armed) {
        if (!armed) {
            if (t.gps_fix_type >= 3 && (t.lat || t.lon)) {
                spotGround.push([t.lat, t.lon]);
                if (spotGround.length > 10) spotGround.shift();
            }
            if (spotCapturing) spotFinishClimb('disarmed');
        } else if (spotLastArmed === false && spotOn) {
            spotStartClimb(t);
        }
        spotLastArmed = armed;
        if (!spotCapturing || !armed) return;
        const off = spotOffset(t);
        if (spotNext >= SPOT_HEIGHTS.length) spotFinishClimb('');
        else if ((t.alt_rel || 0) >= SPOT_HANDBACK_ALT) spotFinishClimb('reached ' + SPOT_HANDBACK_ALT + ' m');
        else if (off && Math.hypot(off.n, off.e) > SPOT_AWAY_M) spotFinishClimb('the Solo moved away from the spot');
    }

    // One climb picture, once the Solo is high enough for the next one
    function spotCapture(t, pyr, cam) {
        if (Date.now() - spotDownTime < SPOT_GIMBAL_MS || spotNext >= SPOT_HEIGHTS.length) return;
        const view = spotView(t), target = SPOT_HEIGHTS[spotNext], off = spotOffset(t);
        if (view.height < target || !off) return;
        // climbed past it (slow phone, video gap): a picture from higher up is no use low down
        const late = view.height > target * SPOT_HEIGHT_WINDOW;
        const res = late ? { ok: false, reason: spotLastReason || 'climbed past it before a picture was taken' }
                         : SpotFinder.reference(pyr, cam, view, -off.n, -off.e);
        if (res.ok) {
            spotRefs.push(res.ref);
            spotNext++;
            spotLastReason = '';
            setSpotButton();
            setPrecisionMsg('TAKEOFF SPOT: ' + spotRefs.length + ' of 3 pictures saved (' + target + ' m).', false);
            spotLog('TAKEOFF SPOT: picture ' + spotRefs.length + ' saved at ' + view.height.toFixed(1) + ' m (for ' +
                    target + ' m), ' + Math.hypot(off.n, off.e).toFixed(1) + ' m from the spot', true);
        } else if (late) {
            spotNotes.push(target + ' m: ' + res.reason);
            spotNext++;
            spotLastReason = '';
            setPrecisionMsg('TAKEOFF SPOT: no picture at ' + target + ' m (' + res.reason + ').', true);
            spotLog('TAKEOFF SPOT: no picture at ' + target + ' m (' + res.reason + '), now ' + view.height.toFixed(1) + ' m', true);
            if (spotNext < SPOT_HEIGHTS.length) {
                spotCapture(t, pyr, cam);          // the next height may be due now
                return;
            }
        } else {
            spotLastReason = res.reason;           // tried again on the next frame
        }
        if (spotNext >= SPOT_HEIGHTS.length) spotFinishClimb('');
    }

    // Look for the spot while armed near it, after the climb
    function spotWanted(t) {
        if (!spotOn || spotCapturing || !spotRefs.length || !telemArmed(t)) return false;
        const off = spotOffset(t);
        return !!off && Math.hypot(off.n, off.e) < SPOT_NEAR_M + (t.alt_rel || 0);
    }

    // Match this frame; only a match agreeing with the ones before it goes to the Cube.
    // -> {m (SpotFinder.match), send}
    function spotLook(t, pyr, cam) {
        const view = spotView(t), off = spotOffset(t);
        const gn = -off.n, ge = -off.e, now = Date.now();
        if (spotTrack && now - spotTrack.time > PAD_LOST_MS) spotTrack = null;
        const tracking = spotTrack && spotTrack.count >= SPOT_AGREE_COUNT;
        const prior = tracking
            ? { n: gn + spotTrack.dn, e: ge + spotTrack.de, radius: SPOT_TRACK_M + 0.05 * view.height, scale: spotTrack.scale }
            : { n: gn, e: ge, radius: SPOT_SEARCH_M, scale: spotTrack ? spotTrack.scale : 1 };
        const m = SpotFinder.match(pyr, cam, view, spotRefs, prior);
        spotCount(view, m, null, false);
        if (!m.ok) return { m: m, send: false };
        const dn = m.n - gn, de = m.e - ge;
        const r = SpotFinder.track(spotTrack, dn, de, view.height, now, {
            agreeM: SPOT_AGREE_M, agreeCount: SPOT_AGREE_COUNT, lostMs: PAD_LOST_MS, steadyMs: SPOT_STEADY_MS });
        spotTrack = r.st;
        if (r.event !== 'miss') spotTrack.scale = m.scale;
        spotCount(view, m, r.event, r.send);
        return { m: m, send: r.send };
    }

    // The way down: count what each look found, and once a second put a line in the Solo's log
    function spotCount(view, m, event, send) {
        const now = Date.now();
        if (!spotStats) spotStats = { since: now, looks: 0, matched: 0, agree: 0, miss: 0, reset: 0, sent: 0,
                                      best: 0, next: 0, reasons: {}, height: 0 };
        const s = spotStats;
        if (event === null) {                     // a look
            s.looks++;
            s.height = view.height;
            if (m.ok) {
                s.matched++;
                if ((m.score || 0) > s.best) { s.best = m.score || 0; s.next = m.second || 0; }
            } else {
                const why = m.reason || 'no match';
                s.reasons[why] = (s.reasons[why] || 0) + 1;
            }
        } else {                                  // what the tracker made of a match
            if (event === 'agree') s.agree++;
            else if (event === 'miss') s.miss++;
            else if (event === 'reset') s.reset++;
            if (send) s.sent++;
        }
        if (now - s.since < SPOT_LOG_MS) return;
        const why = Object.keys(s.reasons).map(function(k) { return k + ' x' + s.reasons[k]; }).join(', ');
        spotLog('TAKEOFF SPOT looking at ' + s.height.toFixed(1) + ' m: ' + s.looks + ' looks, ' + s.matched +
                ' matched (best score ' + s.best.toFixed(2) + ', next ' + s.next.toFixed(2) + '), ' + s.agree +
                ' agreed, ' + s.miss + ' disagreed, ' + s.reset + ' restarts, ' + s.sent + ' sent to the Cube' +
                (why ? '; not matched: ' + why : ''), false);
        spotStats = null;
    }

    // A line in the Solo's log (/log/solowebutility.log); show: also in the Cockpit's messages
    function spotLog(text, show) {
        fetch('/api/precland/note', {
            method: 'POST',
            headers: authHeaders({ 'Content-Type': 'application/json' }),
            body: JSON.stringify({ text: text, show: !!show })
        }).catch(function() {});
    }

    if (btnTakeoffSpot) {
        btnTakeoffSpot.addEventListener('click', function(e) {
            e.stopPropagation();
            spotOn = !spotOn;
            setPrecisionLabel();
            padLastFrameTime = -1;
            const t = currentTelem;
            if (spotOn) {
                speak('Takeoff spot on');
                checkPrecisionSetup(false);
                if (telemArmed(t) && !spotRefs.length && (t.alt_rel || 0) < 0.5) {
                    spotStartClimb(t);                 // armed, still on the ground
                } else if (telemArmed(t) && !spotRefs.length) {
                    setPrecisionMsg('TAKEOFF SPOT is on, but the Solo is already flying: turn it on before takeoff.', true);
                } else if (spotRefs.length) {
                    setPrecisionMsg('TAKEOFF SPOT on: ' + spotRefs.length + ' of 3 pictures of the takeoff spot saved. ' +
                                    'LAND, RTL and Smart RTL land on it.', false);
                } else {
                    setPrecisionMsg('TAKEOFF SPOT on: when you arm, the camera turns down and the climb saves pictures ' +
                                    'of the takeoff spot (2, 4, 8 m). The camera goes back to the paddle by 10 m.', false);
                }
            } else {
                spotFinishClimb('turned off');
                spotTrack = null;
                clearPadOverlay();
                setPrecisionMsg('TAKEOFF SPOT off.', false);
                speak('Takeoff spot off');
            }
            setSpotButton();
        });
    }

    function precisionAction(body, doneText) {
        return postAction(body).then(function(data) {
            const ok = data && data.status === 'ok';
            setPrecisionMsg((data && data.message) || (ok ? doneText : 'Command failed.'), !ok);
        }).catch(function(err) {
            setPrecisionMsg('Command failed: ' + err.message, true);
        });
    }

    if (btnCameraDown) {
        btnCameraDown.addEventListener('click', function(e) {
            e.stopPropagation();
            precisionAction({ action: 'camera_down' }, 'Camera pointed down.');
        });
    }
    if (btnPrecLoiter) {
        btnPrecLoiter.addEventListener('click', function(e) {
            e.stopPropagation();
            const turnOn = padServer.loiter !== true;
            precisionAction({ action: 'prec_loiter', mode: turnOn ? 'on' : 'off' }, 'Precision Loiter ' + (turnOn ? 'on.' : 'off.'));
        });
    }
    if (btnPrecLand) {
        btnPrecLand.addEventListener('click', function(e) {
            e.stopPropagation();
            speak('Mode land');
            precisionAction({ action: 'set_mode', mode: 'LAND' }, 'LAND sent.');
        });
    }

    function clearPadOverlay() {
        if (!padOverlayCtx) return;
        padOverlayCtx.setTransform(1, 0, 0, 1, 0, 0);
        padOverlayCtx.clearRect(0, 0, padOverlay.width, padOverlay.height);
    }

    // Outline the pad (or the takeoff spot's picture, in blue) on the video and draw a line from
    // straight below the Solo to its centre
    function drawPadOverlay(det, m, fw, fh, spot) {
        if (!padOverlayCtx) return;
        const dpr = window.devicePixelRatio || 1;
        const cw = padOverlay.clientWidth, ch = padOverlay.clientHeight;
        if (padOverlay.width !== Math.round(cw * dpr) || padOverlay.height !== Math.round(ch * dpr)) {
            padOverlay.width = Math.round(cw * dpr);
            padOverlay.height = Math.round(ch * dpr);
        }
        clearPadOverlay();
        if (!det || !cw || !ch) return;
        // the video fills the viewport like object-fit: cover
        const k = Math.max(cw / fw, ch / fh);
        const ox = (cw - fw * k) / 2, oy = (ch - fh * k) / 2;
        const P = function(p) { return [ox + p[0] * k, oy + p[1] * k]; };
        const ctx = padOverlayCtx;
        const colour = spot ? '#38bdf8' : '#10b981';
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        ctx.lineWidth = 3;
        ctx.strokeStyle = colour;
        if (det.corners) {
            ctx.beginPath();
            det.corners.forEach(function(p, i) {
                const q = P(p);
                if (i === 0) ctx.moveTo(q[0], q[1]); else ctx.lineTo(q[0], q[1]);
            });
            ctx.closePath();
            ctx.stroke();
        }
        if (det.corners && !spot) {
            const tl = P(det.corners[0]);
            ctx.fillStyle = colour;
            ctx.beginPath();
            ctx.arc(tl[0], tl[1], 5, 0, Math.PI * 2);
            ctx.fill();
        }
        const c = P(det.center), mid = P([fw / 2, fh / 2]);
        ctx.setLineDash([6, 4]);
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.moveTo(mid[0], mid[1]);
        ctx.lineTo(c[0], c[1]);
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.beginPath();
        ctx.arc(c[0], c[1], 6, 0, Math.PI * 2);
        ctx.stroke();
        if (m) {
            const alt = getConvertedAlt(m.below);
            const label = alt.val.toFixed(1) + ' ' + alt.unit;
            ctx.font = 'bold 15px sans-serif';
            ctx.lineWidth = 4;
            ctx.strokeStyle = '#000';
            ctx.strokeText(label, c[0] + 10, c[1] - 10);
            ctx.fillStyle = spot ? '#e0f2fe' : '#d1fae5';
            ctx.fillText(label, c[0] + 10, c[1] - 10);
        }
    }

    function sendPadSighting(m, t0, source) {
        if (padPostBusy) return;            // never queue up: the next frame is newer anyway
        padPostBusy = true;
        fetch('/api/precland/target', {
            method: 'POST',
            headers: authHeaders({ 'Content-Type': 'application/json' }),
            body: JSON.stringify({
                angle_x: m.angle_x, angle_y: m.angle_y, distance: m.distance, size: m.size,
                age_ms: Math.round(performance.now() - t0), source: source || 'pad',
                delay_s: padDelayS + (performance.now() - t0) / 1000
            })
        }).then(function(r) {
            if (r.status === 401) authRequired();
        }).catch(function() {}).then(function() {
            padPostBusy = false;
        });
    }

    // "PAD 3.2 m below, 0.4 m ahead, 0.1 m right · Cube gets 9/s" (name: PAD or SPOT)
    function showTargetStatus(name, m, sending) {
        if (name === 'SPOT' && Date.now() < spotMsgUntil) return;
        const notReady = padCheck && !padCheck.ready;
        const below = getConvertedAlt(m.below);
        const ahead = getConvertedAlt(Math.abs(m.ahead)), right = getConvertedAlt(Math.abs(m.right));
        let text = name + ' ' + below.val.toFixed(1) + ' ' + below.unit + ' below, ' +
            ahead.val.toFixed(1) + ' ' + ahead.unit + (m.ahead >= 0 ? ' ahead, ' : ' behind, ') +
            right.val.toFixed(1) + ' ' + right.unit + (m.right >= 0 ? ' right' : ' left');
        if (!sending) text += ' · checking (' + (spotTrack ? spotTrack.count : 0) + ' of ' + SPOT_AGREE_COUNT + ')';
        else if (notReady) text += '. ' + padCheck.message;
        else text += padServer.sending ? ' · Cube gets ' + padServer.rate + '/s' : ' · sending';
        setPrecisionMsg(text, !!(sending && notReady));
    }

    // Nothing seen for a while: say what is being looked for
    function showLookingStatus(spotMatch, spotActive) {
        const now = Date.now();
        if (now - padLastSeen < PAD_LOST_MS || now - spotLastSeen < PAD_LOST_MS || now < spotMsgUntil) return;
        const notReady = padCheck && !padCheck.ready;
        if (notReady) { setPrecisionMsg(padCheck.message, true); return; }
        if (!spotActive) {
            setPrecisionMsg('Looking for the pad. Point the camera down and fly over it.', false);
            return;
        }
        const text = (padFinderOn ? 'Looking for the pad and the takeoff spot' : 'Looking for the takeoff spot') +
            (spotMatch && spotMatch.reason ? ' (' + spotMatch.reason + ')' : '');
        setPrecisionMsg(text + '. Nothing is sent until it is seen; the Solo lands on GPS.', false);
    }

    // ~10 looks a second at the live video: the pad first, then the takeoff spot (or, on the
    // climb, the takeoff spot pictures)
    function padTick() {
        const t = currentTelem;
        const spotActive = typeof SpotFinder !== 'undefined' && !!t && (spotCapturing || spotWanted(t));
        if (!padFinderOn && !spotActive) return;
        const v = liveVideoFeed;
        if (v && v.seeking) return;               // a jump to the newest video: frames again in a moment
        if (!videoWanted() || !v || v.readyState < 2 || !v.videoWidth ||
                Date.now() - lastVideoPacketTime > PAD_VIDEO_FRESH_MS) {
            if (!padNoVideoSince) padNoVideoSince = Date.now();
            if (Date.now() - padNoVideoSince >= PAD_NO_VIDEO_MSG_MS) {
                clearPadOverlay();
                setPrecisionMsg((padFinderOn ? 'Pad finder' : 'TAKEOFF SPOT') +
                                ' on, but there is no live video (is Solex using it?). Nothing is sent.', true);
            }
            return;
        }
        padNoVideoSince = 0;
        if (v.currentTime === padLastFrameTime) return;          // no new frame yet
        padLastFrameTime = v.currentTime;
        const t0 = performance.now();
        const scale = Math.min(1, padWorkWidth / v.videoWidth);
        const w = Math.round(v.videoWidth * scale), h = Math.round(v.videoHeight * scale);
        if (padCanvas.width !== w || padCanvas.height !== h) {
            padCanvas.width = w;
            padCanvas.height = h;
        }
        let det = null, m = null, spot = null;
        try {
            padCtx.drawImage(v, 0, 0, w, h);
            padGray = PadFinder.toGray(padCtx.getImageData(0, 0, w, h).data, w, h, padGray);
            const cam = PadFinder.camera(w, h, padFovDeg, padLens);
            if (padFinderOn) {
                det = PadFinder.detect(padGray, w, h);
                if (det) m = PadFinder.measure(det, cam, padSizeM);
            }
            if (!m && spotActive) {
                const pyr = SpotFinder.pyramid(padGray, w, h);
                if (spotCapturing) spotCapture(t, pyr, cam);
                else spot = spotLook(t, pyr, cam);
            }
        } catch (err) {
            console.warn('[PadFinder]', err);
        }
        const took = performance.now() - t0;
        padSlowMs = padSlowMs ? padSlowMs * 0.9 + took * 0.1 : took;
        if (padSlowMs > PAD_SLOW_MS && padWorkWidth > PAD_WORK_WIDTH_MIN) {
            padWorkWidth = PAD_WORK_WIDTH_MIN;      // slow phone: smaller frames (shorter range)
            padSlowMs = 0;
        }
        if (m) {
            drawPadOverlay(det, m, w, h);
            if (Date.now() - padLastSeen > 3000) speak('Pad found');
            padLastSeen = Date.now();
            sendPadSighting(m, t0, 'pad');
            showTargetStatus('PAD', m, true);
        } else if (spot && spot.m.ok) {
            drawPadOverlay(spot.m.center ? { corners: spot.m.outline, center: spot.m.center } : null, spot.m, w, h, true);
            if (spot.send) {
                if (Date.now() - spotLastSeen > 3000) speak('Takeoff spot found');
                spotLastSeen = Date.now();
                sendPadSighting(spot.m, t0, 'spot');
            }
            showTargetStatus('SPOT', spot.m, spot.send);
        } else {
            drawPadOverlay(null, null, w, h);
            if (!spotCapturing) showLookingStatus(spot && spot.m, spotActive);
        }
    }
    if (typeof PadFinder !== 'undefined') setInterval(padTick, PAD_TICK_MS);

    // Telemetry carries what the server sent (telemetry.precland)
    function updatePrecision(telem) {
        padServer = telem.precland || {};
        if (btnPrecLoiter) {
            const on = padServer.loiter === true;
            btnPrecLoiter.classList.toggle('active', on);
            btnPrecLoiter.setAttribute('aria-pressed', on ? 'true' : 'false');
            btnPrecLoiter.textContent = 'PREC LOITER: ' + (on ? 'ON' : 'OFF');
        }
        const armed = telemArmed(telem);
        if (btnPrecLand) btnPrecLand.disabled = !armed;
        updateTakeoffSpot(telem, armed);
        // camera straight down as the Solo starts to land (LAND, RTL, Smart RTL) with the finder
        // on, or with pictures of the takeoff spot saved
        const mode = telem.flight_mode || '';
        const looking = padFinderOn || (spotOn && spotRefs.length > 0);
        if (looking && armed && padLastMode !== null && mode !== padLastMode && PAD_LAND_MODES.indexOf(mode) !== -1) {
            postAction({ action: 'camera_down' }).catch(function() {});
        }
        padLastMode = mode;
    }

    const btnFullscreen = document.getElementById('btnFullscreen');
    if (btnFullscreen) {
        btnFullscreen.addEventListener('click', function() {
            if (!document.fullscreenElement) {
                document.documentElement.requestFullscreen().catch(() => {});
            } else {
                document.exitFullscreen().catch(() => {});
            }
        });
    }

    if (btnScrollBottom) {
        btnScrollBottom.addEventListener('click', function(e) {
            e.stopPropagation();
            mavlinkMsgBox.scrollTop = mavlinkMsgBox.scrollHeight;
        });
    }

    if (btnExpandMsgs) {
        btnExpandMsgs.addEventListener('click', function(e) {
            e.stopPropagation();
            const isMaximized = leftHud.classList.toggle('messages-maximized');
            this.textContent = isMaximized ? '⤡' : '⤢';
            this.title = isMaximized ? 'Restore View' : 'Maximize Message Window';
            setTimeout(() => {
                mavlinkMsgBox.scrollTop = mavlinkMsgBox.scrollHeight;
            }, 60);
        });
    }

    if (btnClearMsgs) {
        btnClearMsgs.addEventListener('click', function(e) {
            e.stopPropagation();
            mavlinkMsgBox.innerHTML = '<div class="mav-line"><span class="mav-time">[' + new Date().toTimeString().split(' ')[0] + ']</span>Console view cleared.</div>';
            lastRenderedMsgKey = 'cleared_' + Date.now();
        });
    }

    if (btnToggleCompass && cardCompass) {
        btnToggleCompass.addEventListener('click', function(e) {
            e.stopPropagation();
            const isCollapsed = cardCompass.classList.toggle('collapsed');
            this.textContent = isCollapsed ? '▸' : '▾';
            setTimeout(resizeCanvases, 50);
        });
    }

    if (btnVideoToggle) {
        btnVideoToggle.addEventListener('click', function(e) {
            e.stopPropagation();
            videoModeActive = !videoModeActive;
            hudDirty = true;
            this.classList.toggle('active', videoModeActive);
            this.textContent = videoModeActive ? '📹 VIDEO' : '🧭 HORIZON';
            if (liveVideoFeed) {
                liveVideoFeed.style.display = videoModeActive ? 'block' : 'none';
            }
            if (videoStandbyOverlay) {
                if (!videoModeActive || videoActive) {
                    videoStandbyOverlay.classList.add('hidden');
                } else {
                    videoStandbyOverlay.classList.remove('hidden');
                }
            }
        });
    }

    // =========================================================================
    // Primary Flight Display (PFD) Renderer
    // Full bleed canvas across the entire viewport behind overlays!
    // =========================================================================
    function drawPFD(telem) {
        if (!telem) return;
        const dpr = window.devicePixelRatio || 1;
        const w = pfdCanvas.width / dpr;
        const h = pfdCanvas.height / dpr;
        if (w <= 0 || h <= 0) return;
        const cx = w / 2;
        const cy = h / 2;

        const att = telem.attitude || {};
        const hud = telem.hud || {};
        const pitch = (typeof att.pitch === 'number') ? att.pitch : ((typeof telem.pitch === 'number') ? telem.pitch : 0);
        const roll = (typeof att.roll === 'number') ? att.roll : ((typeof telem.roll === 'number') ? telem.roll : 0);
        const rollRad = (roll * Math.PI) / 180.0;
        const heading = (typeof hud.heading === 'number') ? hud.heading : ((typeof telem.heading === 'number') ? telem.heading : 0);

        const gspd = (typeof hud.groundspeed === 'number') ? hud.groundspeed : ((typeof telem.groundspeed === 'number') ? telem.groundspeed : 0);
        const altRel = (typeof hud.alt_rel === 'number') ? hud.alt_rel : ((typeof telem.alt_rel === 'number') ? telem.alt_rel : 0);
        const climb = (typeof hud.climb_rate === 'number') ? hud.climb_rate : ((typeof telem.climb_rate === 'number') ? telem.climb_rate : 0);

        const speedObj = getConvertedSpeed(gspd);
        const altObj = getConvertedAlt(altRel);

        pfdCtx.save();
        pfdCtx.clearRect(0, 0, w, h);

        // Positioning of Tapes: Frame center horizon between HUD overlays
        const tapeWidth = 50;
        const tapeTop = 26;
        
        // When overlays are visible, tapes sit just inside the overlay margins
        const leftMargin = hudVisible ? 222 : 6;
        const rightMargin = hudVisible ? 212 : 6;

        const tapeLeftX = leftMargin;
        const tapeRightX = w - rightMargin - tapeWidth - 18;

        // 1. Full-bleed Horizon behind everything
        pfdCtx.save();
        pfdCtx.translate(cx, cy);
        pfdCtx.rotate(-rollRad);

        const pitchPxPerDeg = 3.6;
        const pitchOffset = pitch * pitchPxPerDeg;
        const diag = Math.sqrt(w * w + h * h) * 1.5;

        if (!videoModeActive) {
            // Sky gradient (Artificial Horizon Mode)
            const skyGrad = pfdCtx.createLinearGradient(0, -diag + pitchOffset, 0, pitchOffset);
            skyGrad.addColorStop(0, '#1d4ed8');
            skyGrad.addColorStop(1, '#3b82f6');
            pfdCtx.fillStyle = skyGrad;
            pfdCtx.fillRect(-diag, -diag + pitchOffset, diag * 2, diag);

            // Earth gradient (Artificial Horizon Mode)
            const groundGrad = pfdCtx.createLinearGradient(0, pitchOffset, 0, diag + pitchOffset);
            groundGrad.addColorStop(0, '#78350f');
            groundGrad.addColorStop(1, '#3e1a02');
            pfdCtx.fillStyle = groundGrad;
            pfdCtx.fillRect(-diag, pitchOffset, diag * 2, diag);
        } else {
            // Live Video Mode: Transparent canvas background so live video feed shows through!
            // Subtle orientation vignette below the horizon
            const groundGrad = pfdCtx.createLinearGradient(0, pitchOffset, 0, diag + pitchOffset);
            groundGrad.addColorStop(0, 'rgba(0, 0, 0, 0.0)');
            groundGrad.addColorStop(1, 'rgba(0, 0, 0, 0.0)');
            pfdCtx.fillStyle = groundGrad;
            pfdCtx.fillRect(-diag, pitchOffset, diag * 2, diag);
        }

        // Horizon line (high visibility cyan in video mode)
        pfdCtx.strokeStyle = videoModeActive ? '#38bdf8' : '#ffffff';
        pfdCtx.lineWidth = 2.0;
        pfdCtx.beginPath();
        pfdCtx.moveTo(-diag, pitchOffset);
        pfdCtx.lineTo(diag, pitchOffset);
        pfdCtx.stroke();

        // Pitch Ladder
        pfdCtx.fillStyle = '#ffffff';
        pfdCtx.font = 'bold 9px monospace';
        pfdCtx.textAlign = 'center';
        pfdCtx.textBaseline = 'middle';

        for (let deg = -30; deg <= 30; deg += 5) {
            if (deg === 0) continue;
            const y = pitchOffset - (deg * pitchPxPerDeg);
            const isMajor = (deg % 10 === 0);
            const halfW = isMajor ? 26 : 14;

            pfdCtx.lineWidth = isMajor ? 1.8 : 1.0;
            pfdCtx.beginPath();

            if (deg > 0) {
                pfdCtx.moveTo(-halfW, y);
                pfdCtx.lineTo(halfW, y);
                pfdCtx.moveTo(-halfW, y);
                pfdCtx.lineTo(-halfW, y + 4);
                pfdCtx.moveTo(halfW, y);
                pfdCtx.lineTo(halfW, y + 4);
                pfdCtx.stroke();
            } else {
                pfdCtx.setLineDash([4, 3]);
                pfdCtx.moveTo(-halfW, y);
                pfdCtx.lineTo(halfW, y);
                pfdCtx.stroke();
                pfdCtx.setLineDash([]);
                pfdCtx.beginPath();
                pfdCtx.moveTo(-halfW, y);
                pfdCtx.lineTo(-halfW, y - 4);
                pfdCtx.moveTo(halfW, y);
                pfdCtx.lineTo(halfW, y - 4);
                pfdCtx.stroke();
            }

            if (isMajor) {
                pfdCtx.fillText(Math.abs(deg).toString(), -halfW - 10, y);
                pfdCtx.fillText(Math.abs(deg).toString(), halfW + 10, y);
            }
        }

        pfdCtx.restore(); // Undo horizon translate & rotate

        // 2. Roll Scale & Pointer
        drawRollScale(pfdCtx, cx, cy, rollRad, h);

        // 3. Fixed Center Aircraft Boresight Reticle (Yellow Chevrons)
        pfdCtx.save();
        pfdCtx.translate(cx, cy);
        pfdCtx.fillStyle = '#f59e0b';
        pfdCtx.strokeStyle = '#000000';
        pfdCtx.lineWidth = 1.5;

        // Boresight dot
        pfdCtx.beginPath();
        pfdCtx.arc(0, 0, 3, 0, Math.PI * 2);
        pfdCtx.fill();
        pfdCtx.stroke();

        // Left wing
        pfdCtx.beginPath();
        pfdCtx.rect(-34, -2.5, 24, 5);
        pfdCtx.fill();
        pfdCtx.stroke();
        pfdCtx.beginPath();
        pfdCtx.rect(-34, 2.5, 5, 6);
        pfdCtx.fill();
        pfdCtx.stroke();

        // Right wing
        pfdCtx.beginPath();
        pfdCtx.rect(10, -2.5, 24, 5);
        pfdCtx.fill();
        pfdCtx.stroke();
        pfdCtx.beginPath();
        pfdCtx.rect(29, 2.5, 5, 6);
        pfdCtx.fill();
        pfdCtx.stroke();

        pfdCtx.restore();

        // 4. Airspeed Tape (Metric m/s or Imperial mph)
        drawAirspeedTape(pfdCtx, tapeLeftX, tapeTop, tapeWidth, h - tapeTop, speedObj.val, speedObj.unit);

        // 5. Altitude Tape (Metric m or Imperial ft)
        drawAltitudeTape(pfdCtx, tapeRightX, tapeTop, tapeWidth, h - tapeTop, altObj.val, altObj.unit);

        // 6. Right Edge VSI Variometer Tape
        drawVSITape(pfdCtx, tapeRightX + tapeWidth, tapeTop, 18, h - tapeTop, climb);

        // 7. Top Rolling Heading Tape
        drawHeadingTape(pfdCtx, tapeLeftX + tapeWidth, 0, tapeRightX - (tapeLeftX + tapeWidth), tapeTop, heading);

        // 8. Flight Mode OSD
        const fMode = (telem.status && telem.status.flight_mode) ? telem.status.flight_mode : (telem.flight_mode || 'UNKNOWN');
        pfdCtx.save();
        pfdCtx.fillStyle = '#10b981';
        pfdCtx.font = '900 18px "Consolas", "SFMono-Regular", monospace';
        pfdCtx.textAlign = 'center';
        pfdCtx.shadowColor = '#000';
        pfdCtx.shadowOffsetX = 2;
        pfdCtx.shadowOffsetY = 2;
        pfdCtx.shadowBlur = 2;
        pfdCtx.fillText(fMode.toUpperCase(), cx, h - 30);
        
        // Also draw ARM status next to it if we want, or just mode. Mode is enough.
        pfdCtx.restore();


        pfdCtx.restore();
    }

    function drawRollScale(ctx, cx, cy, rollRad, h) {
        ctx.save();
        const r = Math.min(105, (h / 2) - 15);
        ctx.translate(cx, cy);

        ctx.strokeStyle = '#10b981';
        ctx.lineWidth = 1.2;
        const marks = [-60, -45, -30, -20, -10, 0, 10, 20, 30, 45, 60];

        marks.forEach(deg => {
            const rad = ((deg - 90) * Math.PI) / 180;
            const len = (deg === 0 || Math.abs(deg) === 30 || Math.abs(deg) === 60) ? 9 : 5;
            const x1 = Math.cos(rad) * r;
            const y1 = Math.sin(rad) * r;
            const x2 = Math.cos(rad) * (r - len);
            const y2 = Math.sin(rad) * (r - len);

            ctx.beginPath();
            ctx.moveTo(x1, y1);
            ctx.lineTo(x2, y2);
            ctx.stroke();
        });

        // Top zero triangle
        ctx.fillStyle = '#10b981';
        ctx.beginPath();
        ctx.moveTo(0, -r + 1);
        ctx.lineTo(-4, -r - 6);
        ctx.lineTo(4, -r - 6);
        ctx.closePath();
        ctx.fill();

        // Dynamic roll pointer
        ctx.rotate(-rollRad);
        ctx.fillStyle = '#f59e0b';
        ctx.beginPath();
        ctx.moveTo(0, -r + 10);
        ctx.lineTo(-4.5, -r + 1);
        ctx.lineTo(4.5, -r + 1);
        ctx.closePath();
        ctx.fill();

        ctx.restore();
    }

    function drawAirspeedTape(ctx, x, y, w, h, speed, unitLabel) {
        ctx.save();
        // transparent tape

        const cy = y + h / 2;
        const pxPerUnit = (unitSystem === 'imperial') ? 5 : 10;

        ctx.strokeStyle = '#10b981';
        ctx.fillStyle = '#94a3b8';
        ctx.font = '9px monospace';
        ctx.textAlign = 'right';

        const range = (unitSystem === 'imperial') ? 16 : 10;
        const minS = Math.max(0, Math.floor(speed - range));
        const maxS = Math.ceil(speed + range);
        const majorStep = (unitSystem === 'imperial') ? 5 : 5;

        for (let s = minS; s <= maxS; s++) {
            const ty = cy - (s - speed) * pxPerUnit;
            if (ty < y || ty > y + h) continue;

            const isMajor = (s % majorStep === 0);
            ctx.lineWidth = isMajor ? 1.5 : 1;
            ctx.beginPath();
            ctx.moveTo(x + w, ty);
            ctx.lineTo(x + w - (isMajor ? 10 : 5), ty);
            ctx.stroke();

            if (isMajor) {
                ctx.fillText(s.toString(), x + w - 12, ty + 3);
            }
        }

        // Digital box
        ctx.fillStyle = '#0f172a';
        ctx.strokeStyle = '#38bdf8';
        ctx.lineWidth = 1.2;
        ctx.beginPath();
        ctx.rect(x + 2, cy - 12, w - 4, 24);
        ctx.fill();
        ctx.stroke();

        ctx.fillStyle = '#38bdf8';
        ctx.font = 'bold 11px monospace';
        ctx.textAlign = 'center';
        ctx.fillText(speed.toFixed(1), x + (w / 2), cy + 4);

        ctx.fillStyle = '#64748b';
        ctx.font = 'bold 8px sans-serif';
        ctx.fillText(unitLabel.toUpperCase(), x + (w / 2), y + 10);

        ctx.restore();
    }

    function drawAltitudeTape(ctx, x, y, w, h, alt, unitLabel) {
        ctx.save();
        // transparent tape

        const cy = y + h / 2;
        const pxPerUnit = (unitSystem === 'imperial') ? 2.5 : 6;

        ctx.strokeStyle = '#10b981';
        ctx.fillStyle = '#94a3b8';
        ctx.font = '9px monospace';
        ctx.textAlign = 'left';

        const range = (unitSystem === 'imperial') ? 40 : 18;
        const minA = Math.max(0, Math.floor(alt - range));
        const maxA = Math.ceil(alt + range);
        const majorStep = (unitSystem === 'imperial') ? 20 : 10;
        const midStep = (unitSystem === 'imperial') ? 10 : 5;

        for (let a = minA; a <= maxA; a++) {
            const ty = cy - (a - alt) * pxPerUnit;
            if (ty < y || ty > y + h) continue;

            const isMajor = (a % majorStep === 0);
            const isMid = (a % midStep === 0);
            if (!isMajor && !isMid) continue;

            ctx.lineWidth = isMajor ? 1.5 : 1;
            ctx.beginPath();
            ctx.moveTo(x, ty);
            ctx.lineTo(x + (isMajor ? 10 : 5), ty);
            ctx.stroke();

            if (isMajor) {
                ctx.fillText(a.toString(), x + 12, ty + 3);
            }
        }

        // Digital box
        ctx.fillStyle = '#0f172a';
        ctx.strokeStyle = '#10b981';
        ctx.lineWidth = 1.2;
        ctx.beginPath();
        ctx.rect(x + 2, cy - 12, w - 4, 24);
        ctx.fill();
        ctx.stroke();

        ctx.fillStyle = '#10b981';
        ctx.font = 'bold 11px monospace';
        ctx.textAlign = 'center';
        ctx.fillText(alt.toFixed(1), x + (w / 2), cy + 4);

        ctx.fillStyle = '#64748b';
        ctx.font = 'bold 8px sans-serif';
        ctx.fillText('ALT ' + unitLabel.toUpperCase(), x + (w / 2), y + 10);

        ctx.restore();
    }

    function drawVSITape(ctx, x, y, w, h, climb) {
        ctx.save();
        // transparent tape

        const cy = y + h / 2;
        ctx.strokeStyle = '#64748b';
        ctx.beginPath();
        ctx.moveTo(x, cy);
        ctx.lineTo(x + w, cy);
        ctx.stroke();

        const maxC = 4.0;
        const clamped = Math.max(-maxC, Math.min(maxC, climb));
        const py = cy - (clamped / maxC) * (h / 2 - 8);

        ctx.fillStyle = climb >= 0 ? '#10b981' : '#ef4444';
        ctx.beginPath();
        ctx.moveTo(x + 2, py);
        ctx.lineTo(x + w - 2, py);
        ctx.lineTo(x + 2, py + (climb >= 0 ? 4 : -4));
        ctx.closePath();
        ctx.fill();

        ctx.restore();
    }

    function drawHeadingTape(ctx, x, y, w, h, heading) {
        ctx.save();
        // transparent tape

        const cx = x + w / 2;
        const pxPerDeg = 2.4;

        ctx.fillStyle = '#10b981';
        ctx.font = 'bold 9px monospace';
        ctx.textAlign = 'center';

        const map = { 0: 'N', 360: 'N', 45: 'NE', 90: 'E', 135: 'SE', 180: 'S', 225: 'SW', 270: 'W', 315: 'NW' };

        for (let deg = -60; deg <= 420; deg += 5) {
            const norm = (deg + 360) % 360;
            const diff = deg - heading;
            const tx = cx + diff * pxPerDeg;

            if (tx < x || tx > x + w) continue;

            const isCard = (norm in map && norm % 45 === 0);
            const is10 = (norm % 10 === 0);

            ctx.strokeStyle = isCard ? '#38bdf8' : '#ffffff';
            ctx.lineWidth = (isCard || is10) ? 1.5 : 0.8;
            ctx.beginPath();
            ctx.moveTo(tx, y + h);
            ctx.lineTo(tx, y + h - (isCard ? 9 : (is10 ? 6 : 3)));
            ctx.stroke();

            if (isCard) {
                ctx.fillStyle = norm === 0 ? '#ef4444' : '#38bdf8';
                ctx.fillText(map[norm], tx, y + 11);
                ctx.fillStyle = '#10b981';
            } else if (is10) {
                ctx.fillText((norm / 10).toString().padStart(2, '0'), tx, y + 11);
            }
        }

        // Center cursor triangle
        ctx.fillStyle = '#f59e0b';
        ctx.beginPath();
        ctx.moveTo(cx, y + h);
        ctx.lineTo(cx - 4, y + h - 6);
        ctx.lineTo(cx + 4, y + h - 6);
        ctx.closePath();
        ctx.fill();

        ctx.restore();
    }

    // =========================================================================
    // Mini HSI Compass Rose Renderer
    // =========================================================================
    function drawMiniHSI(telem) {
        if (!telem) return;
        const dpr = window.devicePixelRatio || 1;
        const w = hsiCanvas.width / dpr;
        const h = hsiCanvas.height / dpr;
        if (w <= 0 || h <= 0) return;
        const cx = w / 2;
        const cy = h / 2;
        const r = Math.min(w, h) / 2 - 4;

        const hud = telem.hud || {};
        const gps = telem.gps || {};
        const heading = (typeof hud.heading === 'number') ? hud.heading : ((typeof telem.heading === 'number') ? telem.heading : 0);
        const headingRad = (heading * Math.PI) / 180.0;
        const homeBearing = (typeof gps.bearing_to_home === 'number') ? gps.bearing_to_home : ((typeof telem.bearing_to_home === 'number') ? telem.bearing_to_home : 0);

        hsiCtx.save();
        hsiCtx.clearRect(0, 0, w, h);

        hsiCtx.beginPath();
        hsiCtx.arc(cx, cy, r, 0, Math.PI * 2);
        hsiCtx.fillStyle = '#090e1a';
        hsiCtx.fill();
        hsiCtx.strokeStyle = '#1e293b';
        hsiCtx.lineWidth = 1.5;
        hsiCtx.stroke();

        // Rotating compass rose
        hsiCtx.save();
        hsiCtx.translate(cx, cy);
        hsiCtx.rotate(-headingRad);

        const card = { 0: 'N', 360: 'N', 90: 'E', 180: 'S', 270: 'W' };

        for (let deg = 0; deg < 360; deg += 10) {
            const rad = (deg * Math.PI) / 180;
            const isC = (deg in card);
            const is30 = (deg % 30 === 0);
            const len = isC ? 7 : (is30 ? 5 : 3);

            const x1 = Math.sin(rad) * r;
            const y1 = -Math.cos(rad) * r;
            const x2 = Math.sin(rad) * (r - len);
            const y2 = -Math.cos(rad) * (r - len);

            hsiCtx.strokeStyle = isC ? '#38bdf8' : '#64748b';
            hsiCtx.lineWidth = isC ? 1.5 : 1;
            hsiCtx.beginPath();
            hsiCtx.moveTo(x1, y1);
            hsiCtx.lineTo(x2, y2);
            hsiCtx.stroke();

            if (isC) {
                hsiCtx.fillStyle = deg === 0 ? '#ef4444' : '#38bdf8';
                hsiCtx.font = 'bold 8px sans-serif';
                hsiCtx.textAlign = 'center';
                hsiCtx.textBaseline = 'middle';
                hsiCtx.fillText(card[deg], Math.sin(rad) * (r - 12), -Math.cos(rad) * (r - 12));
            }
        }

        // Home Bearing Bug (Green arrow on ring)
        if (telem.gps.distance_to_home > 1.0) {
            const bugRad = (homeBearing * Math.PI) / 180;
            hsiCtx.save();
            hsiCtx.rotate(bugRad);
            hsiCtx.fillStyle = '#10b981';
            hsiCtx.beginPath();
            hsiCtx.moveTo(0, -r);
            hsiCtx.lineTo(-3.5, -r + 7);
            hsiCtx.lineTo(3.5, -r + 7);
            hsiCtx.closePath();
            hsiCtx.fill();
            hsiCtx.restore();
        }

        hsiCtx.restore(); // Undo rose rotate

        // Top lubber line
        hsiCtx.fillStyle = '#f59e0b';
        hsiCtx.beginPath();
        hsiCtx.moveTo(cx, cy - r);
        hsiCtx.lineTo(cx - 3.5, cy - r + 6);
        hsiCtx.lineTo(cx + 3.5, cy - r + 6);
        hsiCtx.closePath();
        hsiCtx.fill();

        // Center aircraft dot
        hsiCtx.fillStyle = '#f59e0b';
        hsiCtx.beginPath();
        hsiCtx.arc(cx, cy, 2.5, 0, Math.PI * 2);
        hsiCtx.fill();

        hsiCtx.restore();
    }

    // =========================================================================
    // UI Update & DOM Sync
    // =========================================================================
    // =========================================================================
    // MAVLink OSD Messages Bar
    // =========================================================================
    const osdBar = document.getElementById('osdMsgBar');
    const osdMain = document.getElementById('osdMsgMain');
    const osdSev = document.getElementById('osdMsgSev');
    const osdText = document.getElementById('osdMsgText');
    const osdAge = document.getElementById('osdMsgAge');
    const osdHistory = document.getElementById('osdMsgHistory');
    const osdViewport = document.getElementById('pfdViewport');
    const OSD_BOTTOM = 50;        // px above the bottom of the video: one line above the flight mode text
    const OSD_SEV_LABELS = ['EMERG', 'ALERT', 'CRIT', 'ERROR', 'WARN', 'NOTICE', 'INFO', 'DEBUG'];

    function osdSevClass(sev) {
        if (sev <= 3) return 'osd-sev-crit';
        if (sev === 4) return 'osd-sev-warn';
        if (sev >= 7) return 'osd-sev-debug';
        return 'osd-sev-info';
    }

    function osdAgeText(secs) {
        if (secs < 2) return 'now';
        if (secs < 60) return Math.floor(secs) + 's';
        if (secs < 3600) return Math.floor(secs / 60) + 'm';
        return Math.floor(secs / 3600) + 'h';
    }

    // Keep the bar between the speed and altitude tapes (they move when the HUD panels hide)
    function layoutOsdBar() {
        if (!osdBar || !osdViewport) return;
        const w = osdViewport.clientWidth || 0;
        let left = hudVisible ? 280 : 64;           // panel + speed tape + gap
        let right = hudVisible ? 288 : 82;          // panel + altitude tape + VSI + gap
        if (w - left - right < 260) {               // narrow screens: use the full width
            left = 8;
            right = 8;
        }
        // Bottom of the video, one line above the flight mode; the history opens upwards (CSS).
        const key = left + '_' + right;
        if (key !== osdLayoutKey) {
            osdLayoutKey = key;
            osdBar.style.left = left + 'px';
            osdBar.style.right = right + 'px';
            osdBar.style.top = 'auto';
            osdBar.style.bottom = OSD_BOTTOM + 'px';
        }
    }

    function renderOsdHistory(list) {
        if (!osdHistory) return;
        if (!list.length) {
            osdHistory.innerHTML = '<div class="osd-hist-empty">No vehicle messages yet.</div>';
            return;
        }
        let html = '';
        for (let i = list.length - 1; i >= 0; i--) {          // newest first
            const m = list[i];
            const sev = (typeof m.severity === 'number') ? m.severity : 6;
            html += '<div class="osd-hist-row ' + osdSevClass(sev) + '">' +
                '<span class="osd-hist-time">' + escapeHtml(m.time || '') + '</span>' +
                '<span class="osd-hist-sev">' + (OSD_SEV_LABELS[sev] || 'INFO') + '</span>' +
                '<span class="osd-hist-text">' + escapeHtml(m.text || '') + '</span></div>';
        }
        osdHistory.innerHTML = html;
    }

    function updateOsdBar(telem) {
        if (!osdBar) return;
        layoutOsdBar();
        const osd = telem.osd || {};
        const list = Array.isArray(osd.messages) ? osd.messages : [];
        const latest = osd.latest || (list.length ? list[list.length - 1] : null);

        // The utility restarted: message ids start over
        if (osd.session && osd.session !== osdSession) {
            osdSession = osd.session;
            osdLastId = 0;
        }

        if (!latest) {
            osdBar.className = 'osd-msg-bar osd-empty';
            osdSev.textContent = 'MSG';
            osdText.textContent = 'No vehicle messages yet';
            osdAge.textContent = '';
            if (osdBar.classList.contains('osd-open')) renderOsdHistory(list);
            return;
        }

        const sev = (typeof latest.severity === 'number') ? latest.severity : 6;
        const isNew = latest.id !== osdLastId;
        // Age from the Solo's own clock, so a phone with a different clock still fades correctly
        const serverNow = (typeof telem.server_time === 'number') ? telem.server_time : (Date.now() / 1000);
        const age = Math.max(0, serverNow - (latest.timestamp || serverNow));
        const activeSecs = sev <= 4 ? OSD_ACTIVE_SECS_ALERT : OSD_ACTIVE_SECS_INFO;
        const open = osdBar.classList.contains('osd-open');

        let cls = 'osd-msg-bar ' + osdSevClass(sev);
        if (age > activeSecs) cls += ' osd-idle';
        if (open) cls += ' osd-open';
        if (isNew && osdLastId !== 0 && age < activeSecs) {
            cls += ' osd-new';
            if (osdNewTimer) clearTimeout(osdNewTimer);
            osdNewTimer = setTimeout(function() { osdBar.classList.remove('osd-new'); }, 950);
        } else if (osdBar.classList.contains('osd-new')) {
            cls += ' osd-new';
        }
        if (osdBar.className !== cls) osdBar.className = cls;

        if (isNew) {
            osdLastId = latest.id;
            osdSev.textContent = OSD_SEV_LABELS[sev] || 'INFO';
            osdText.textContent = latest.text || '';
            osdMain.title = (latest.time ? '[' + latest.time + '] ' : '') + (latest.text || '') + '  (click for recent messages)';
            if (open) renderOsdHistory(list);
        }
        osdAge.textContent = osdAgeText(age);
    }

    if (osdMain) {
        osdMain.addEventListener('click', function(e) {
            e.stopPropagation();
            const open = osdBar.classList.toggle('osd-open');
            osdMain.setAttribute('aria-expanded', open ? 'true' : 'false');
            osdHistory.hidden = !open;
            if (open) {
                const osd = (currentTelem && currentTelem.osd) || {};
                renderOsdHistory(Array.isArray(osd.messages) ? osd.messages : []);
            }
        });
        // Clicking anywhere else closes the history
        document.addEventListener('click', function(e) {
            if (osdBar.classList.contains('osd-open') && !osdBar.contains(e.target)) {
                osdBar.classList.remove('osd-open');
                osdMain.setAttribute('aria-expanded', 'false');
                osdHistory.hidden = true;
            }
        });
    }

    function updateDOM(telem) {
        if (!telem) return;
        try {
            const status = telem.status || {};
            const battery = telem.battery || {};
            const gps = telem.gps || {};
            const hud = telem.hud || {};

            const isArmed = status.armed ?? telem.armed ?? false;
            const flightMode = status.flight_mode || telem.flight_mode || 'UNKNOWN';
            const copterEndpoint = status.copter_endpoint || '127.0.0.1:14560';
            const pktRate = (typeof status.packet_rate_hz === 'number') ? status.packet_rate_hz : ((typeof telem.packet_rate === 'number') ? telem.packet_rate : 0);

            // Arming Pill & Button
            if (isArmed) {
                armPill.textContent = 'ARMED';
                armPill.className = 'pill pill-armed active';
                btnArm.textContent = 'DISARM';
                btnArm.className = 'btn-cmd btn-arm armed';
            } else {
                armPill.textContent = 'DISARMED';
                armPill.className = 'pill pill-armed';
                btnArm.textContent = 'ARM MOTORS';
                btnArm.className = 'btn-cmd btn-arm';
            }

            // Flight Mode Pill & Buttons
            modePill.textContent = flightMode;
            document.querySelectorAll('.mode-btn-group .btn-cmd').forEach(btn => {
                if (btn.textContent.trim().toUpperCase() === flightMode.toUpperCase()) {
                    btn.classList.add('active');
                } else {
                    btn.classList.remove('active');
                }
            });

            // Quick Top Battery & GPS Pills
            const rawPct = (typeof battery.remaining_pct === 'number') ? battery.remaining_pct : ((typeof telem.battery_remaining === 'number') ? telem.battery_remaining : 0);
            const pct = Math.round(rawPct);
            const rawV = (typeof battery.voltage === 'number') ? battery.voltage : ((typeof telem.voltage === 'number') ? telem.voltage : 0);
            const v = rawV.toFixed(1);
            batPill.textContent = '⚡ ' + v + 'V ' + pct + '%';

            const sats = (typeof gps.satellites === 'number') ? gps.satellites : ((typeof telem.satellites === 'number') ? telem.satellites : 0);
            const fixType = (typeof gps.fix_type === 'number') ? gps.fix_type : ((typeof telem.gps_fix_type === 'number') ? telem.gps_fix_type : 0);
            const fixLabel = gps.fix_label || telem.gps_fix_label || (fixType >= 2 ? '3D FIX' : 'NO FIX');
            const pillFix = fixType === 6 ? 'RTK FIX' : fixType === 5 ? 'RTK FLOAT' : fixType === 4 ? 'DGPS' : (fixType >= 2 ? '3D' : 'NO FIX');
            gpsPill.textContent = '🛰️ ' + sats + ' ' + pillFix;

            // MAVLink Connection Badge
            const isOnline = telem.mavlink_online ?? telem.connected ?? telem.mavlink_connected ?? false;
            if (isOnline) {
                linkPill.className = 'pill pill-live';
                linkDot.className = 'live-dot';
                linkTxt.textContent = 'LIVE (' + copterEndpoint + ') ' + pktRate.toFixed(1) + 'Hz';
            } else if (telem.source === 'simulation') {
                linkPill.className = 'pill pill-live';
                linkDot.className = 'live-dot';
                linkTxt.textContent = 'SIMULATION FLIGHT';
            } else {
                linkPill.className = 'pill pill-live offline';
                linkDot.className = 'live-dot offline';
                linkTxt.textContent = 'Awaiting Copter (' + copterEndpoint + ')';
            }

            // Left HUD Readouts (with Unit Conversions)
            const heading = (typeof hud.heading === 'number') ? hud.heading : ((typeof telem.heading === 'number') ? telem.heading : 0);
            hsiHdgTxt.textContent = Math.round((heading % 360 + 360) % 360).toString().padStart(3, '0') + '°';

            const distVal = (typeof gps.distance_to_home === 'number') ? gps.distance_to_home : ((typeof telem.distance_to_home === 'number') ? telem.distance_to_home : 0);
            const brgVal = (typeof gps.bearing_to_home === 'number') ? gps.bearing_to_home : ((typeof telem.bearing_to_home === 'number') ? telem.bearing_to_home : 0);
            const distObj = getConvertedDist(distVal);
            homeDistVal.textContent = distObj.val;
            homeBrgVal.textContent = Math.round((brgVal % 360 + 360) % 360).toString().padStart(3, '0');

            // GPS Section
            gpsFixTxt.textContent = fixLabel.toUpperCase();
            gpsSatsCount.textContent = sats + ' SATS';
            if (sats >= 10) gpsSatsCount.style.color = 'var(--accent-emerald)';
            else if (sats >= 6) gpsSatsCount.style.color = 'var(--accent-amber)';
            else gpsSatsCount.style.color = 'var(--accent-red)';

            const latVal = (typeof gps.lat === 'number') ? gps.lat : ((typeof telem.lat === 'number') ? telem.lat : 0);
            const lonVal = (typeof gps.lon === 'number') ? gps.lon : ((typeof telem.lon === 'number') ? telem.lon : 0);
            gpsLatVal.textContent = latVal.toFixed(6);
            gpsLonVal.textContent = lonVal.toFixed(6);

            const altMsl = (typeof hud.alt_msl === 'number') ? hud.alt_msl : ((typeof telem.alt_msl === 'number') ? telem.alt_msl : 0);
            const altMslObj = getConvertedAlt(altMsl);
            altMslVal.textContent = altMslObj.val.toFixed(1);

            const hdopVal = (typeof gps.hdop === 'number') ? gps.hdop : ((typeof telem.hdop === 'number') ? telem.hdop : 0);
            gpsHdopVal.textContent = hdopVal.toFixed(2);
            try { updateRtk(telem); } catch (e) { console.warn('RTK row', e); }   // never stop the HUD

            // MAVLink Messages Box
            const msgs = msgLog;
            if (msgCountBadge) {
                msgCountBadge.textContent = msgs.length + ' MSGS';
            }
            if (msgs.length > 0) {
                const lastMsg = msgs[msgs.length - 1];
                const lastMsgKey = msgs.length + '_' + (lastMsg.time || '') + '_' + (lastMsg.text || '');
                if (lastMsgKey !== lastRenderedMsgKey) {
                    lastRenderedMsgKey = lastMsgKey;
                    const isNearBottom = (mavlinkMsgBox.scrollHeight - mavlinkMsgBox.scrollTop - mavlinkMsgBox.clientHeight) < 60;
                    let html = '';
                    for (let i = 0; i < msgs.length; i++) {
                        const m = msgs[i];
                        let cls = 'mav-line';
                        if (m.severity <= 3) cls += ' mav-crit';
                        else if (m.severity <= 4) cls += ' mav-warn';
                        html += '<div class="' + cls + '"><span class="mav-time">[' + (m.time || '') + ']</span>' + escapeHtml(m.text || '') + '</div>';
                    }
                    mavlinkMsgBox.innerHTML = html;
                    if (isNearBottom || msgs.length === 1) {
                        mavlinkMsgBox.scrollTop = mavlinkMsgBox.scrollHeight;
                    }
                }
            }

            updateSmartShots(telem);
            updatePrecision(telem);

            // MAVLink OSD messages bar (vehicle STATUSTEXT)
            updateOsdBar(telem);

            // Right HUD Battery
            batVoltsBig.textContent = rawV.toFixed(2) + ' V';
            batPctBig.textContent = pct + '%';
            const tempVal = (typeof battery.temperature_c === 'number') ? battery.temperature_c : ((typeof telem.battery_temp === 'number') ? telem.battery_temp : 25);
            batTempTxt.textContent = tempVal.toFixed(1) + '°C';
            batBarFill.style.width = pct + '%';
            let bColor = 'var(--accent-emerald)';
            if (pct <= 20) bColor = 'var(--accent-red)';
            else if (pct <= 40) bColor = 'var(--accent-amber)';
            batBarFill.style.background = bColor;
            batPctBig.style.color = bColor;

            const ampsVal = (typeof battery.current === 'number') ? battery.current : ((typeof telem.current === 'number') ? telem.current : 0);
            batAmpsVal.textContent = ampsVal.toFixed(1);
            const consumedVal = (typeof battery.consumed_mah === 'number') ? battery.consumed_mah : ((typeof telem.consumed_mah === 'number') ? telem.consumed_mah : 0);
            batConsumedVal.textContent = consumedVal;
            const flightTime = (typeof battery.flight_time_min === 'number') ? battery.flight_time_min : ((typeof telem.est_flight_time_min === 'number') ? telem.est_flight_time_min : 0);
            flightTimeVal.textContent = flightTime.toFixed(1);

            // Cell Voltages
            const rawCellV = (typeof battery.cell_voltage === 'number') ? battery.cell_voltage : ((typeof telem.cell_voltage === 'number') ? telem.cell_voltage : (rawV / 4));
            const cv = rawCellV.toFixed(2) + 'V';
            c1Val.textContent = cv;
            c2Val.textContent = cv;
            c3Val.textContent = cv;
            c4Val.textContent = cv;

            // Health & Speeds on Right HUD
            pktRateTxt.textContent = pktRate.toFixed(1) + ' Hz';
            const rcRssi = (typeof status.rssi_pct === 'number') ? status.rssi_pct : ((typeof telem.rssi === 'number') ? telem.rssi : 100);
            rcRssiVal.textContent = rcRssi;
            const vibe = (typeof status.vibe_clip === 'number') ? status.vibe_clip : 0;
            vibeVal.textContent = vibe;

            const aspd = (typeof hud.airspeed === 'number') ? hud.airspeed : ((typeof telem.airspeed === 'number') ? telem.airspeed : 0);
            const aspdObj = getConvertedSpeed(aspd);
            airspeedHudVal.textContent = aspdObj.val.toFixed(1);

            const gspd = (typeof hud.groundspeed === 'number') ? hud.groundspeed : ((typeof telem.groundspeed === 'number') ? telem.groundspeed : 0);
            const gspdObj = getConvertedSpeed(gspd);
            groundspeedHudVal.textContent = gspdObj.val.toFixed(1);

            // Video Status indicator from server telemetry
            const vid = telem.video || {};
            const vActive = Boolean(vid.streaming_active || (Date.now() - lastVideoPacketTime < 3000));
            if (videoDot) videoDot.classList.toggle('offline', !vActive);
            const appIp = vid.yielded_to_app || null;
            if (videoTxt) {
                if (vActive) {
                    const rateStr = vid.kbps > 0 ? (' ' + Math.round(vid.kbps) + 'k') : '';
                    videoTxt.textContent = 'HDMI LIVE' + rateStr;
                } else {
                    videoTxt.textContent = appIp ? 'VIDEO IN SOLEX' : 'NO VIDEO';
                }
            }
            if (videoSignalBadge && !vActive) {
                videoSignalBadge.textContent = appIp
                    ? ('VIDEO IS WITH SOLEX (' + appIp + ') - IT COMES BACK HERE WHEN SOLEX CLOSES')
                    : 'WAITING FOR HDMI FEED';
            }
            if (vActive && videoStandbyOverlay) {
                videoStandbyOverlay.classList.add('hidden');
            }

            // Voice alerts
            if (lastSpokenMode !== flightMode) {
                speak("Mode " + flightMode);
                lastSpokenMode = flightMode;
            }

            if (lastSpokenArmed !== isArmed) {
                speak(isArmed ? "Motors Armed" : "Motors Disarmed");
                lastSpokenArmed = isArmed;
            }

            if (pct <= 20 && pct > 0 && !lastSpokenLowBat) {
                speak("Low battery, " + pct + " percent");
                lastSpokenLowBat = true;
            } else if (pct > 25) {
                lastSpokenLowBat = false;
            }

            if (pct <= 10 && pct > 0 && !lastSpokenCritBat) {
                speak("Critical battery, land now");
                lastSpokenCritBat = true;
            } else if (pct > 15) {
                lastSpokenCritBat = false;
            }
        } catch (err) {
            console.error('[Cockpit] updateDOM error:', err);
        }
    }

    // =========================================================================
    // Direct High-Frequency Telemetry Polling (10 Hz)
    // =========================================================================
    function initStream() {
        let isPolling = false;

        function pollTelemetry() {
            if (isPolling) return;
            // Page hidden (e.g. switched to Solex): stop polling so the Solo's MAVLink link
            // goes idle and Solex has it to itself.
            if (document.visibilityState === 'hidden') {
                setTimeout(pollTelemetry, 1000);
                return;
            }
            isPolling = true;

            // The message log is only sent when it changed since msgLogSeq (saves ~1 Mbit/s of Wi-Fi)
            fetch('/api/telemetry?msgs_since=' + msgLogSeq, { cache: 'no-store', headers: authHeaders() })
                .then(r => {
                    if (r.status === 401) { authRequired(); throw new Error('Login required'); }
                    if (!r.ok) throw new Error('HTTP ' + r.status);
                    return r.json();
                })
                .then(data => {
                    isPolling = false;
                    if (Array.isArray(data.mavlink_messages)) {
                        msgLog = data.mavlink_messages;
                        msgLogSeq = (typeof data.messages_seq === 'number') ? data.messages_seq : -1;
                    }
                    currentTelem = data;
                    hudDirty = true;
                    try {
                        updateDOM(data);
                    } catch (domErr) {
                        console.error('[Cockpit] DOM sync error:', domErr);
                    }
                    setTimeout(pollTelemetry, 100);
                })
                .catch((err) => {
                    isPolling = false;
                    setTimeout(pollTelemetry, (err && err.message === 'Login required') ? 2000 : 600);
                });
        }

        pollTelemetry();
    }

    // =========================================================================
    // Live HDMI Video Pipeline (JMuxer + Fetch ReadableStream)
    // =========================================================================
    let h264StreamStarted = false;

    function initVideo() {
        if (typeof JMuxer === 'undefined') {
            console.warn('[Video] JMuxer library not loaded yet, retrying in 250ms...');
            setTimeout(initVideo, 250);
            return;
        }
        if (jmuxer) return;

        if (liveVideoFeed) {
            liveVideoFeed.muted = true;
            liveVideoFeed.defaultMuted = true;
            liveVideoFeed.playsInline = true;
            liveVideoFeed.autoplay = true;

            liveVideoFeed.addEventListener('playing', function() {
                console.log('[Video] liveVideoFeed is playing at', liveVideoFeed.videoWidth + 'x' + liveVideoFeed.videoHeight);
                if (videoStandbyOverlay) videoStandbyOverlay.classList.add('hidden');
            });
            liveVideoFeed.addEventListener('error', function(e) {
                console.error('[Video] liveVideoFeed element error:', liveVideoFeed.error);
            });
        }

        try {
            jmuxer = new JMuxer({
                node: 'liveVideoFeed',
                mode: 'video',
                flushingTime: 0,
                clearBuffer: false,
                fps: VIDEO_FPS,          // the Solo encodes at 24 fps (sololink.conf videoMinFR/MaxFR)
                maxDelay: 500,
                debug: false,
                onError: function(data) {
                    console.warn('[Video] JMuxer buffer error:', data);
                }
            });
            if (!h264StreamStarted) {
                h264StreamStarted = true;
                startH264Stream();
            }
        } catch (e) {
            console.error('[Video] Error initializing JMuxer:', e);
        }
    }

    // Video is only pulled while the Cockpit tab is showing and the page is visible:
    // a backgrounded phone tab or another utility tab costs the Solo nothing.
    function videoWanted() {
        const cockpit = document.getElementById('tab-cockpit');
        return document.visibilityState === 'visible' && !!cockpit && cockpit.classList.contains('active');
    }

    let videoAbort = null;          // AbortController of the running /api/video/h264 fetch
    let lastVideoByteTime = 0;
    const VIDEO_STALL_MS = 3000;    // no bytes for this long: drop the connection and reconnect

    function stopVideoFetch() {
        if (videoAbort) {
            try { videoAbort.abort(); } catch (e) {}
            videoAbort = null;
        }
    }

    async function startH264Stream() {
        while (true) {
            if (!videoWanted()) {
                await new Promise(r => setTimeout(r, 400));
                continue;
            }
            const ctrl = (typeof AbortController !== 'undefined') ? new AbortController() : null;
            videoAbort = ctrl;
            lastVideoByteTime = Date.now();
            try {
                const vopts = { cache: 'no-store', headers: authHeaders() };
                if (ctrl) vopts.signal = ctrl.signal;
                const response = await fetch('/api/video/h264', vopts);
                if (response.status === 401) {
                    authRequired();
                    await new Promise(r => setTimeout(r, 3000));
                    continue;
                }
                if (!response.body) {
                    await new Promise(r => setTimeout(r, 1500));
                    continue;
                }
                const reader = response.body.getReader();
                while (true) {
                    const { value, done } = await reader.read();
                    if (done) break;
                    if (value && value.length > 0) {
                        lastVideoByteTime = Date.now();
                        if (jmuxer) {
                            jmuxer.feed({ video: value });
                        }
                        onVideoPacketReceived();
                    }
                }
                await new Promise(r => setTimeout(r, 300));
            } catch (err) {
                if (!(err && err.name === 'AbortError')) {
                    console.warn('[Video] Stream fetch error, reconnecting...', err);
                }
                await new Promise(r => setTimeout(r, 1000));
            } finally {
                if (videoAbort === ctrl) videoAbort = null;
            }
        }
    }

    // Stall watchdog: a half-dead Wi-Fi connection can hang a fetch indefinitely
    setInterval(function() {
        if (!videoAbort) return;
        if (!videoWanted()) {
            stopVideoFetch();                       // tab hidden / left the Cockpit
        } else if (Date.now() - lastVideoByteTime > VIDEO_STALL_MS) {
            console.warn('[Video] No data for ' + VIDEO_STALL_MS + ' ms, reconnecting');
            stopVideoFetch();
        }
    }, 1000);

    // Live-edge control: always play the newest video. JMuxer's own check only looks at the
    // first buffered range, so after a reconnect gap the video could stay seconds behind.
    let liveEdgeJumps = 0;
    setInterval(function() {
        const v = liveVideoFeed;
        if (!v || !v.buffered || v.buffered.length === 0 || v.seeking) return;
        const last = v.buffered.length - 1;
        const end = v.buffered.end(last);
        const lag = end - v.currentTime;
        // The pad finder and the takeoff spot steer the Solo by this video: keep it nearer live.
        // Small lags are made up by playing a little faster; only a big one is jumped, because a
        // jump makes the browser decode again from the last key frame and the video stalls (the
        // finders then see no frame, and the Cube no sighting).
        const finders = padFinderOn || spotOn;
        if (lag > VIDEO_MAX_LAG_S || v.currentTime < v.buffered.start(last)) {
            try {
                v.currentTime = Math.max(v.buffered.start(last), end - 0.05);
                liveEdgeJumps++;
            } catch (e) {}
            if (v.paused && videoWanted()) v.play().catch(function() {});
        }
        const rate = finders && lag > VIDEO_FINDER_MAX_LAG_S ? VIDEO_CATCHUP_RATE : 1.0;
        if (v.playbackRate !== rate) {
            try { v.playbackRate = rate; } catch (e) {}
        }
    }, 250);

    document.addEventListener('visibilitychange', function() {
        if (!videoWanted()) {
            stopVideoFetch();
        } else if (liveVideoFeed && liveVideoFeed.paused) {
            liveVideoFeed.play().catch(function() {});
        }
    });

    function onVideoPacketReceived() {
        lastVideoPacketTime = Date.now();
        if (!videoActive) {
            videoActive = true;
            if (videoDot) videoDot.classList.remove('offline');
            if (videoTxt) videoTxt.textContent = 'HDMI LIVE';
        }
        if (liveVideoFeed) {
            if (liveVideoFeed.paused) {
                liveVideoFeed.play().catch(function(err) {
                    console.log('[Video] Autoplay blocked, awaiting user click:', err);
                    if (videoSignalBadge) videoSignalBadge.textContent = 'CLICK HUD TO ACTIVATE VIDEO';
                });
            }
            if (liveVideoFeed.videoWidth > 0 && videoStandbyOverlay) {
                videoStandbyOverlay.classList.add('hidden');
            }
        }
    }

    // Video liveness watchdog (resets to standby after 3.5s without packets)
    setInterval(function() {
        const now = Date.now();
        if (videoActive && (now - lastVideoPacketTime > 3500)) {
            videoActive = false;
            if (videoStandbyOverlay && videoModeActive) videoStandbyOverlay.classList.remove('hidden');
            if (videoDot) videoDot.classList.add('offline');
            if (videoTxt) videoTxt.textContent = 'NO VIDEO';
            if (videoSignalBadge) videoSignalBadge.textContent = 'WAITING FOR HDMI FEED';
        }
    }, 1000);

    // =========================================================================
    // 60 FPS Resilient Animation Loop
    // =========================================================================
    function animLoop() {
        try {
            if (currentTelem && hudDirty) {
                hudDirty = false;
                drawPFD(currentTelem);
                drawMiniHSI(currentTelem);
            }
        } catch (err) {
            console.error('[Cockpit] animLoop render error:', err);
        }
        requestAnimationFrame(animLoop);
    }

    // Tab Activation Listener for Canvas & Video Viewport Resize
    function handleCockpitActivation() {
        resizeCanvases();
        if (!jmuxer) {
            initVideo();
        } else if (liveVideoFeed && liveVideoFeed.paused) {
            liveVideoFeed.play().catch(function() {});
        }
    }

    document.querySelectorAll('.nav-tab').forEach(function(tab) {
        tab.addEventListener('click', function() {
            const targetId = tab.getAttribute('data-tab');
            if (targetId === 'tab-cockpit') {
                setTimeout(handleCockpitActivation, 60);
                setTimeout(handleCockpitActivation, 300);
            }
        });
    });

    // User gesture handler on cockpit body to ensure unmuted play or resume if paused
    const cockpitSection = document.getElementById('tab-cockpit');
    if (cockpitSection) {
        cockpitSection.addEventListener('click', function() {
            if (liveVideoFeed && liveVideoFeed.paused) {
                liveVideoFeed.play().catch(function() {});
            }
        });
    }

    // Start telemetry stream and canvas animation loop immediately
    initStream();
    requestAnimationFrame(animLoop);

    // If tab-cockpit is active on initial page load, initialize video immediately
    if (cockpitSection && (cockpitSection.classList.contains('active') || window.getComputedStyle(cockpitSection).display !== 'none')) {
        setTimeout(handleCockpitActivation, 100);
    }

    // =========================================================================
    // Global User Actions
    // =========================================================================
    window.sendFlightMode = function(mode) {
        if (typeof speak === 'function') speak("Mode " + mode);
        postAction({ action: 'set_mode', mode: mode, custom_mode: mode }).then(function(data) {
            console.log('[Cockpit] set_mode response:', data);
        }).catch(function(err) {
            console.error('[Cockpit] set_mode failed:', err);
        });
    };

    // =========================================================================
    // 2-Step Arming Safety Verification (Default is CANCEL)
    // =========================================================================
    const armModalBackdrop = document.getElementById('armModalBackdrop');
    const armModalStep1 = document.getElementById('armModalStep1');
    const armModalStep2 = document.getElementById('armModalStep2');
    const btnArmCancel1 = document.getElementById('btnArmCancel1');
    const btnArmCancel2 = document.getElementById('btnArmCancel2');

    window.sendArmToggle = function() {
        // If already confirmed ARMED, disarm immediately (Emergency stop should never be delayed)
        if (currentTelem && currentTelem.status && currentTelem.status.armed) {
            postAction({ action: 'disarm' }).catch(function() {});
            speak("Disarming motors");
            return;
        }

        // Arming: Trigger 2-step safety warning sequence
        openArmStep1();
    };

    function openArmStep1() {
        if (!armModalBackdrop || !armModalStep1) return;
        armModalBackdrop.style.display = 'flex';
        armModalStep1.style.display = 'flex';
        if (armModalStep2) armModalStep2.style.display = 'none';
        // Auto-focus CANCEL as default action
        setTimeout(() => { if (btnArmCancel1) btnArmCancel1.focus(); }, 60);
    }
    window.openArmStep1 = openArmStep1;

    window.openArmStep2 = function() {
        if (!armModalStep1 || !armModalStep2) return;
        armModalStep1.style.display = 'none';
        armModalStep2.style.display = 'flex';
        // Auto-focus CANCEL as default action
        setTimeout(() => { if (btnArmCancel2) btnArmCancel2.focus(); }, 60);
    };

    window.cancelArming = function() {
        if (armModalBackdrop) armModalBackdrop.style.display = 'none';
        if (armModalStep1) armModalStep1.style.display = 'none';
        if (armModalStep2) armModalStep2.style.display = 'none';
    };

    window.executeArmMotors = function() {
        window.cancelArming();
        postAction({ action: 'arm' }).catch(function() {});
        speak("Arm command dispatched");
    };

    // Attach explicit click listeners
    if (btnArm) btnArm.addEventListener('click', window.sendArmToggle);
    if (btnArmCancel1) btnArmCancel1.addEventListener('click', window.cancelArming);
    const btnArmNext1 = document.getElementById('btnArmNext1');
    if (btnArmNext1) btnArmNext1.addEventListener('click', window.openArmStep2);
    if (btnArmCancel2) btnArmCancel2.addEventListener('click', window.cancelArming);
    const btnArmConfirmFinal = document.getElementById('btnArmConfirmFinal');
    if (btnArmConfirmFinal) btnArmConfirmFinal.addEventListener('click', window.executeArmMotors);

    // Close on Escape or click outside
    window.addEventListener('keydown', function(e) {
        if (e.key === 'Escape' && armModalBackdrop && armModalBackdrop.style.display === 'flex') {
            window.cancelArming();
        }
    });

    if (armModalBackdrop) {
        armModalBackdrop.addEventListener('click', function(e) {
            if (e.target === armModalBackdrop) {
                window.cancelArming();
            }
        });
    }

})();
