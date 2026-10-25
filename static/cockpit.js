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
        localStorage.setItem('solo_unit_pref', unitSystem);
        updateUnitButtonLabel();
        if (currentTelem) updateDOM(currentTelem);
    });

    updateUnitButtonLabel();

    // =========================================================================
    // Responsive Canvas Resizing
    // =========================================================================
    function resizeCanvases() {
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
            fetch('/api/video/restart', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' })
                .then(r => r.json())
                .then(() => {
                    setTimeout(() => {
                        btnRestartVideo.textContent = '🔄 RESTART VIDEO';
                        btnRestartVideo.classList.remove('active');
                    }, 2000);
                });
        });
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
        const btns = document.querySelectorAll('#tab-cockpit .top-bar .btn-ctrl');
        let below = 0;
        for (let i = 0; i < btns.length; i++) {
            const r = btns[i].getBoundingClientRect();
            if (!r.width || !r.height) continue;
            const overlapX = r.left < barR && r.right > barL;
            const overlapY = r.top < vp.top + OSD_DEFAULT_TOP + barH && r.bottom > vp.top + OSD_DEFAULT_TOP;
            if (overlapX && overlapY) below = Math.max(below, r.bottom - vp.top + 4);
        }
        if (below) top = Math.round(below);
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
            const msgs = status.messages || telem.mavlink_messages || telem.messages || [];
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
            if (videoTxt) {
                if (vActive) {
                    const rateStr = vid.kbps > 0 ? (' ' + Math.round(vid.kbps) + 'k') : '';
                    videoTxt.textContent = 'HDMI LIVE' + rateStr;
                } else {
                    videoTxt.textContent = 'NO VIDEO';
                }
            }
            if (videoSignalBadge && !vActive) {
                videoSignalBadge.textContent = 'WAITING FOR HDMI FEED';
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
            isPolling = true;

            fetch('/api/telemetry')
                .then(r => {
                    if (!r.ok) throw new Error('HTTP ' + r.status);
                    return r.json();
                })
                .then(data => {
                    isPolling = false;
                    currentTelem = data;
                    try {
                        updateDOM(data);
                    } catch (domErr) {
                        console.error('[Cockpit] DOM sync error:', domErr);
                    }
                    setTimeout(pollTelemetry, 100);
                })
                .catch(() => {
                    isPolling = false;
                    setTimeout(pollTelemetry, 600);
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
                fps: 30,
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

    async function startH264Stream() {
        while (true) {
            try {
                const response = await fetch('/api/video/h264');
                if (!response.body) {
                    await new Promise(r => setTimeout(r, 1500));
                    continue;
                }
                const reader = response.body.getReader();
                while (true) {
                    const { value, done } = await reader.read();
                    if (done) break;
                    if (value && value.length > 0) {
                        if (jmuxer) {
                            jmuxer.feed({ video: value });
                        }
                        onVideoPacketReceived();
                    }
                }
            } catch (err) {
                console.warn('[Video] Stream fetch error, reconnecting...', err);
                await new Promise(r => setTimeout(r, 1500));
            }
        }
    }

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
            if (currentTelem) {
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
        fetch('/api/action', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ action: 'set_mode', mode: mode, custom_mode: mode })
        }).then(function(r) { return r.json(); }).then(function(data) {
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
            fetch('/api/action', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ action: 'disarm' })
            });
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
        fetch('/api/action', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ action: 'arm' })
        });
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
