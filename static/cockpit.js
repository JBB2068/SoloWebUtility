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
    const VIDEO_FINDER_MAX_LAG_S = 0.15;    // ... while the pad finder or TAKEOFF SPOT is on
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
    const PAD_VIDEO_FRESH_MS = 500;      // older video is not used: the Solo has moved on
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
    let spotTrack = null;                // {dn, de: the spot vs GPS (m), count, time, scale}
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
        let pts = spotGround.slice();
        if (!pts.length && t && t.gps_fix_type >= 3 && (t.lat || t.lon)) pts = [[t.lat, t.lon]];
        if (!pts.length) {
            spotHome = null;
            setSpotButton();
            setPrecisionMsg('TAKEOFF SPOT: no GPS fix when armed, so the spot can\'t be saved. The landing uses GPS.', true);
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
        } else if (late) {
            spotNotes.push(target + ' m: ' + res.reason);
            spotNext++;
            spotLastReason = '';
            setPrecisionMsg('TAKEOFF SPOT: no picture at ' + target + ' m (' + res.reason + ').', true);
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
        if (!m.ok) return { m: m, send: false };
        const dn = m.n - gn, de = m.e - ge;
        if (spotTrack && Math.hypot(dn - spotTrack.dn, de - spotTrack.de) < SPOT_AGREE_M + 0.05 * view.height) {
            spotTrack.count++;
        } else {
            spotTrack = { count: 1 };
        }
        spotTrack.dn = dn;
        spotTrack.de = de;
        spotTrack.time = now;
        spotTrack.scale = m.scale;
        return { m: m, send: spotTrack.count >= SPOT_AGREE_COUNT };
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
        if (!videoWanted() || !v || v.readyState < 2 || !v.videoWidth ||
                Date.now() - lastVideoPacketTime > PAD_VIDEO_FRESH_MS) {
            clearPadOverlay();
            setPrecisionMsg((padFinderOn ? 'Pad finder' : 'TAKEOFF SPOT') +
                            ' on, but there is no live video (is Solex using it?). Nothing is sent.', true);
            return;
        }
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
    const OSD_DEFAULT_TOP = 32;   // px below the top of the video: just under the heading tape
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
        // Default spot is just under the heading tape. If the cockpit's own control buttons
        // (they wrap onto extra lines on narrow screens) would cover it, go below them.
        let top = OSD_DEFAULT_TOP;
        const vp = osdViewport.getBoundingClientRect();
        const barRect = osdMain ? osdMain.getBoundingClientRect() : null;
        const barH = barRect && barRect.height ? barRect.height : 28;
        const barL = vp.left + left, barR = vp.right - right;
        // (repeated: below one line of buttons there may be another, e.g. the Smart Shots row)
        const btns = document.querySelectorAll('#tab-cockpit .top-bar .btn-ctrl, #tab-cockpit .top-bar .smart-shot-msg, #tab-cockpit .top-bar .pad-settings');
        for (let pass = 0; pass < 5; pass++) {
            let below = 0;
            for (let i = 0; i < btns.length; i++) {
                const r = btns[i].getBoundingClientRect();
                if (!r.width || !r.height) continue;
                const overlapX = r.left < barR && r.right > barL;
                const overlapY = r.top < vp.top + top + barH && r.bottom > vp.top + top;
                if (overlapX && overlapY) below = Math.max(below, r.bottom - vp.top + 4);
            }
            if (!below) break;
            top = Math.round(below);
        }
        const key = left + '_' + right + '_' + top;
        if (key !== osdLayoutKey) {
            osdLayoutKey = key;
            osdBar.style.left = left + 'px';
            osdBar.style.right = right + 'px';
            osdBar.style.top = top + 'px';
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
            gpsPill.textContent = '🛰️ ' + sats + ' ' + (fixType >= 2 ? '3D' : 'NO FIX');

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
        // the pad finder and the takeoff spot steer the Solo by this video: keep it nearer live
        const maxLag = (padFinderOn || spotOn) ? VIDEO_FINDER_MAX_LAG_S : VIDEO_MAX_LAG_S;
        if (lag > maxLag || v.currentTime < v.buffered.start(last)) {
            try {
                v.currentTime = Math.max(v.buffered.start(last), end - 0.05);
                liveEdgeJumps++;
            } catch (e) {}
            if (v.paused && videoWanted()) v.play().catch(function() {});
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
