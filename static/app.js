/**
 * 3DR Solo Web Utility — Frontend Application v1.0.28
 * Vanilla JS — Zero external dependencies.
 */

(function () {
    'use strict';

    // =========================================================================
    // State
    // =========================================================================
    var authToken = localStorage.getItem('solo_auth_token') || null;
    var allParams = [];
    var modifiedParams = {};
    var flashPollInterval = null;
    var rebootCountdownInterval = null;
    var rebootPingInterval = null;
    var countdownStartTime = 0;
    var timerStarted = false;
    var isUpdateRunning = false;
// Network-signal lock: copter_online readings are ignored until the installation countdown is running.
let installationInProgress = false;
var offlineSinceMs = null;               // when the copter was first seen continuously offline
var REQUIRED_OFFLINE_MS = 15000;         // copter must be offline >= 15s before a 'true' can cancel the timer
function setUploadProgressVisible(visible) {
    var bar = document.getElementById('flashProgressBar');
    var wrap = bar && bar.parentElement;
    if (wrap) { wrap.style.visibility = visible ? 'visible' : 'hidden'; }
}
    var previousCopterOnline = null;

    // =========================================================================
    // DOM helpers
    // =========================================================================
    function el(id) { return document.getElementById(id); }

    var loginModal         = el('loginModal');
    var formLogin          = el('formLogin');
    var inputLoginPassword = el('inputLoginPassword');
    var loginErrorMsg      = el('loginErrorMsg');

    var passwordModal          = el('passwordModal');
    var btnOpenPasswordModal    = el('btnOpenPasswordModal');
    var btnClosePasswordModal   = el('btnClosePasswordModal');
    var formChangePassword      = el('formChangePassword');
    var changePasswordErrorMsg  = el('changePasswordErrorMsg');
    var btnLogout              = el('btnLogout');

    var versionModal        = el('versionModal');
    var btnOpenVersionModal  = el('btnOpenVersionModal');
    var btnCloseVersionModal = el('btnCloseVersionModal');
    var headerVersionTag    = el('headerVersionTag');
    var headerCopterVer     = el('headerCopterVer');
    var footerVersionText   = el('footerVersionText');
    var modalCurrentVersion = el('modalCurrentVersion');
    var modalIterationCount = el('modalIterationCount');
    var inputVersionNote    = el('inputVersionNote');
    var btnIncrementVersion = el('btnIncrementVersion');

    var linkStatusBadge = el('linkStatusBadge');
    var linkStatusText  = el('linkStatusText');
    var appToast        = el('appToast');

    // Safety Interlock elements
    var firmwareArmedLock   = el('firmwareArmedLock');
    var wifiArmedLock       = el('wifiArmedLock');
    var isVehicleArmed      = false;

    // Flash elements
    var btnStartFlash       = el('btnStartFlash');
    var btnResetFlash       = el('btnResetFlash');
    var pipelineStatusBadge = el('pipelineStatusBadge');
    var flashProgressBar    = el('flashProgressBar');
    var flashStageLabel     = el('flashStageLabel');
    var flashPercentLabel   = el('flashPercentLabel');
    var flashTerminal       = el('flashTerminal');
    var btnClearLogs        = el('btnClearLogs');
    var rebootTimerBox      = el('rebootTimerBox');
    var rebootCountdown     = el('rebootCountdown');
    var stepCube            = el('stepCube');
    var stepCopter          = el('stepCopter');
    var stepController      = el('stepController');
    var stepReboot          = el('stepReboot');

    // Firmware Update / APJ Builder elements
    var btnCheckUpdates        = el('btnCheckUpdates');
    var fwActiveBadge          = el('fwActiveBadge');
    var radioDescSolo          = el('radioDescSolo');
    var radioDescGreen         = el('radioDescGreen');
    var fwUpdateModal          = el('fwUpdateModal');
    var btnCloseFwUpdateModal  = el('btnCloseFwUpdateModal');
    var btnFwModalClose        = el('btnFwModalClose');
    var btnFwModalForce        = el('btnFwModalForce');
    var btnFwModalStart        = el('btnFwModalStart');
    var fwModalCurrentVer      = el('fwModalCurrentVer');
    var fwModalOnlineVer       = el('fwModalOnlineVer');
    var fwModalStatusBadge     = el('fwModalStatusBadge');
    var fwModalMessage         = el('fwModalMessage');
    var fwModalProgressArea    = el('fwModalProgressArea');
    var fwModalProgressBar     = el('fwModalProgressBar');
    var fwModalStageLabel      = el('fwModalStageLabel');
    var fwModalPercentLabel    = el('fwModalPercentLabel');
    var fwModalTerminalBox     = el('fwModalTerminalBox');
    var fwModalTerminal        = el('fwModalTerminal');
    var fwWifiPromptSection    = el('fwWifiPromptSection');
    var fwMainSection          = el('fwMainSection');
    var fwWifiSelect           = el('fwWifiSelect');
    var btnScanFwWifi          = el('btnScanFwWifi');
    var fwWifiSsid             = el('fwWifiSsid');
    var fwWifiPassword         = el('fwWifiPassword');
    var btnToggleFwWifiPwd     = el('btnToggleFwWifiPwd');
    var chkShowFwWifiPwd       = el('chkShowFwWifiPwd');
    var fwWifiStatusMsg        = el('fwWifiStatusMsg');
    var btnCancelFwWifi        = el('btnCancelFwWifi');
    var btnConnectFwWifi       = el('btnConnectFwWifi');
    var fwUpdatePollInterval   = null;

    // Wi-Fi elements
    var wifiSelect             = el('wifiSelect');
    var btnScanWifi            = el('btnScanWifi');
    var wifiSsid               = el('wifiSsid');
    var wifiPassword           = el('wifiPassword');
    var btnToggleWifiPwd       = el('btnToggleWifiPwd');
    var chkShowWifiPwd         = el('chkShowWifiPwd');
    var btnConnectWifi         = el('btnConnectWifi');
    var btnResetDefaultWifi= el('btnResetDefaultWifi');
    var chkBidirectional   = el('chkBidirectional');
    var wifiModeBadge      = el('wifiModeBadge');
    var natStatusBadge     = el('natStatusBadge');
    var routeHelperCmd     = el('routeHelperCmd');
    var netStatusSsid      = el('netStatusSsid');
    var netStatusMode      = el('netStatusMode');
    var netStatusIp        = el('netStatusIp');
    var netStatusInternet  = el('netStatusInternet');
    var netStatusFwd       = el('netStatusFwd');
    var netStatusCtrl      = el('netStatusCtrl');
    var netStatusCopterNet = el('netStatusCopterNet');
    var wifiSecurityHint   = el('wifiSecurityHint');
    var wifiProblem        = el('wifiProblem');
    var wifiSteps          = el('wifiSteps');
    var wifiSetupLog       = el('wifiSetupLog');
    var btnRefreshWifiStatus = el('btnRefreshWifiStatus');

    // Parameter elements
    var presetsGrid      = el('presetsGrid');
    var paramSearchInput = el('paramSearchInput');
    var paramSearchCount = el('paramSearchCount');
    var paramTableBody   = el('paramTableBody');
    var btnSaveAllParams = el('btnSaveAllParams');
    var btnExportParam   = el('btnExportParam');
    var btnFetchLive     = el('btnFetchLive');
    var fileImportParam  = el('fileImportParam');

    // =========================================================================
    // Toast
    // =========================================================================
    function showToast(message, isError) {
        appToast.textContent = message;
        appToast.style.borderColor = isError ? '#ef4444' : '#f59e0b';
        appToast.classList.add('show');
        setTimeout(function () { appToast.classList.remove('show'); }, 3800);
    }

    // =========================================================================
    // API helper
    // =========================================================================
    function apiRequest(endpoint, method, body) {
        method = method || 'GET';
        var headers = { 'Content-Type': 'application/json' };
        if (authToken) {
            headers['X-Auth-Token'] = authToken;
            headers['Authorization'] = 'Bearer ' + authToken;
        }

        var url = endpoint;
        if (authToken) {
            var sep = url.indexOf('?') !== -1 ? '&' : '?';
            url = url + sep + 'token=' + encodeURIComponent(authToken);
        }

        var config = {
            method: method,
            headers: headers,
            credentials: 'same-origin'
        };
        if (body && (method === 'POST' || method === 'PUT')) {
            config.body = JSON.stringify(body);
        }

        return fetch(url, config).then(function (resp) {
            if (resp.status === 401) {
                authToken = null;
                localStorage.removeItem('solo_auth_token');
                showLoginModal(true);
                throw new Error('Authentication required');
            }
            if (endpoint === '/api/params/export') { return resp; }
            return resp.json().then(function (data) {
                if (!resp.ok || data.error) {
                    throw new Error(data.message || ('Server error ' + resp.status));
                }
                return data;
            });
        });
    }

    // =========================================================================
    // Auth
    // =========================================================================
    function showLoginModal(show) {
        loginModal.style.display = (show !== false) ? 'flex' : 'none';
        if (show !== false) {
            inputLoginPassword.value = '';
            loginErrorMsg.style.display = 'none';
            inputLoginPassword.focus();
        }
    }

    formLogin.addEventListener('submit', function (e) {
        e.preventDefault();
        var pwd = (inputLoginPassword.value || '').trim();
        fetch('/api/auth/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'same-origin',
            body: JSON.stringify({ password: pwd })
        }).then(function (resp) { return resp.json(); })
          .then(function (data) {
            if (data.success && data.token) {
                authToken = data.token;
                localStorage.setItem('solo_auth_token', authToken);
                showLoginModal(false);
                showToast('Welcome to 3DR Solo Web Utility!');
                initAppData();
            } else {
                loginErrorMsg.textContent = data.message || 'Invalid password.';
                loginErrorMsg.style.display = 'block';
            }
          }).catch(function (err) {
            loginErrorMsg.textContent = 'Connection error: ' + err.message;
            loginErrorMsg.style.display = 'block';
          });
    });

    btnLogout.addEventListener('click', function () {
        apiRequest('/api/auth/logout', 'POST').catch(function(){});
        authToken = null;
        localStorage.removeItem('solo_auth_token');
        showLoginModal(true);
    });

    btnOpenPasswordModal.addEventListener('click', function () {
        changePasswordErrorMsg.style.display = 'none';
        formChangePassword.reset();
        passwordModal.style.display = 'flex';
    });
    btnClosePasswordModal.addEventListener('click', function () {
        passwordModal.style.display = 'none';
    });

    formChangePassword.addEventListener('submit', function (e) {
        e.preventDefault();
        var oldP  = el('inputOldPassword').value;
        var newP  = el('inputNewPassword').value;
        var confP = el('inputConfirmPassword').value;
        if (newP !== confP) {
            changePasswordErrorMsg.textContent = 'New passwords do not match.';
            changePasswordErrorMsg.style.display = 'block';
            return;
        }
        apiRequest('/api/auth/change-password', 'POST', { old_password: oldP, new_password: newP })
            .then(function (data) {
                if (data.token) {
                    authToken = data.token;
                    localStorage.setItem('solo_auth_token', authToken);
                }
                passwordModal.style.display = 'none';
                showToast('🔒 Password changed successfully!');
            }).catch(function (err) {
                changePasswordErrorMsg.textContent = err.message;
                changePasswordErrorMsg.style.display = 'block';
            });
    });

    // =========================================================================
    // Versioning
    // =========================================================================
    function loadVersionInfo() {
        fetch('/api/version').then(function (r) { return r.json(); })
            .then(function (data) {
                var ver  = data.version || '1.0.1';
                var iter = data.iteration || 1;
                headerVersionTag.textContent = 'v' + ver;
                footerVersionText.textContent = 'Version ' + ver + ' (Iteration ' + iter + ')';
                modalCurrentVersion.textContent = ver;
                modalIterationCount.textContent = iter;
            }).catch(function(){});
    }

    if (btnOpenVersionModal) btnOpenVersionModal.addEventListener('click', function () {
        loadVersionInfo();
        versionModal.style.display = 'flex';
    });
    btnCloseVersionModal.addEventListener('click', function () { versionModal.style.display = 'none'; });

    btnIncrementVersion.addEventListener('click', function () {
        var note = inputVersionNote.value.trim() || 'Iteration update';
        apiRequest('/api/version/increment', 'POST', { description: note })
            .then(function (res) {
                showToast('🏷️ Version updated to ' + res.version + ' (Iteration ' + res.iteration + ')');
                inputVersionNote.value = '';
                loadVersionInfo();
            }).catch(function (err) { showToast('Error: ' + err.message, true); });
    });

    // =========================================================================
    // Tab Navigation
    // =========================================================================
    var navTabs = document.querySelectorAll('.nav-tab');
    var tabContents = document.querySelectorAll('.tab-content');

    navTabs.forEach(function (tab) {
        tab.addEventListener('click', function () {
            var targetId = tab.getAttribute('data-tab');
            navTabs.forEach(function (t) { t.classList.remove('active'); });
            tabContents.forEach(function (c) { c.classList.remove('active'); });
            tab.classList.add('active');
            var tgt = el(targetId);
            if (tgt) { tgt.classList.add('active'); }

            if (targetId === 'tab-wifi') {
                // No automatic scan: every scan takes the Controller's only radio off
                // SoloLink's channel for a moment (the copter beeps). Scan on request only.
                refreshWifiStatus();
            }
            else if (targetId === 'tab-params' && allParams.length === 0) {
                loadPresets();
                loadParams();
            } else if (targetId === 'tab-flash') {
                loadFirmwareChoices();
                if (isVehicleArmed) {
                    showToast('⚠️ SAFETY LOCKOUT: Firmware updates are disabled while the vehicle is ARMED.', true);
                }
            }
        });
    });

    // Radio card highlight
    document.querySelectorAll('.radio-group').forEach(function (group) {
        group.addEventListener('change', function (e) {
            if (e.target.type === 'radio') {
                group.querySelectorAll('.radio-card').forEach(function (c) { c.classList.remove('selected'); });
                e.target.closest('.radio-card').classList.add('selected');
            }
        });
    });

    // =========================================================================
    // TAB 1: FIRMWARE FLASHING
    // =========================================================================
    btnStartFlash.addEventListener('click', function () {
        if (isVehicleArmed) {
            showToast('⚠️ SAFETY LOCKOUT: Cannot install firmware while vehicle is ARMED!', true);
            return;
        }
        var cubeType    = document.querySelector('input[name="cubeType"]:checked').value;
        var installMode = document.querySelector('input[name="installMode"]:checked').value;

        var fwSel = el('fwVersionSelect');
        var fwChoice = (fwSel && fwSel.value && fwSel.value !== '__upload__') ? fwSel.value : '';
        var fwLabel = fwChoice ? fwSel.options[fwSel.selectedIndex].text : 'Latest version';
        var fwKind = fwChoice ? (fwSel.options[fwSel.selectedIndex].getAttribute('data-kind') || '') : 'latest';
        var confirmMsg = 'Start firmware installation?\n\n'
            + '• Cube: ' + (cubeType === 'green' ? 'Green Cube' : 'Stock Solo Cube (Black)') + '\n'
            + '• Firmware: ' + fwLabel + '\n'
            + '• Scope: ' + (installMode === 'full' ? 'Full Install (Cube + Copter + Controller)' : 'Cube Only') + '\n\n'
            + ((fwKind === 'previous' || fwKind === 'custom') && installMode === 'full'
                ? 'Tip: to only change the ArduCopter version, "Cube Only" is enough.\n\n' : '')
            + (fwKind === 'custom'
                ? 'Custom file: make sure it was built for this Cube. The flasher refuses a file for a different board.\n\n' : '')
            + 'Ensure Solo and Controller have stable battery power before proceeding.';
        if (!confirm(confirmMsg)) { return; }

        btnStartFlash.disabled = true;
        btnStartFlash.textContent = '⏳ INSTALLATION IN PROGRESS...';
        isUpdateRunning = true;
        installationInProgress = false;
        offlineSinceMs = null;
        setUploadProgressVisible(true);

        var startBody = { cube_type: cubeType, install_mode: installMode };
        if (fwChoice) { startBody.firmware = fwChoice; }
        apiRequest('/api/flash/start', 'POST', startBody)
            .then(function () {
                showToast('🚀 Firmware deployment initiated!');
                startFlashPolling(installMode);
            }).catch(function (err) {
                isUpdateRunning = false;
                showToast('Error starting flash: ' + err.message, true);
                btnStartFlash.disabled = false;
                btnStartFlash.textContent = '🚀 START AUTOMATED INSTALLATION';
            });
    });

    // -------------------------------------------------------------------------
    // Firmware version choice: latest / up to 3 previous versions / custom .apj
    // -------------------------------------------------------------------------
    var fwVersionSelect = el('fwVersionSelect');
    var fwCustomFile    = el('fwCustomFile');
    var fwVersionInfo   = el('fwVersionInfo');
    var FW_UPLOAD_MAX   = 4 * 1024 * 1024;

    function fwSizeText(n) { return n ? (Math.round(n / 1024) + ' KB') : ''; }

    function loadFirmwareChoices(selectPath) {
        if (!fwVersionSelect || !authToken) { return; }
        var checked = document.querySelector('input[name="cubeType"]:checked');
        var cubeType = checked ? checked.value : 'solo';
        apiRequest('/api/flash/firmware?cube_type=' + encodeURIComponent(cubeType))
            .then(function (d) {
                var keep = selectPath || fwVersionSelect.value;
                var html = '';
                if (d.latest) {
                    html += '<option value="" data-kind="latest">Latest: ArduCopter ' + esc(d.latest.version)
                        + ' (' + esc(d.latest.file) + ')</option>';
                } else {
                    html += '<option value="" data-kind="latest">Latest version (none found yet)</option>';
                }
                (d.previous || []).forEach(function (c) {
                    html += '<option value="' + esc(c.path) + '" data-kind="previous">Revert to ArduCopter '
                        + esc(c.version) + ' (' + esc(c.file) + ')</option>';
                });
                (d.custom || []).forEach(function (c) {
                    html += '<option value="' + esc(c.path) + '" data-kind="custom">Custom: ' + esc(c.file)
                        + (c.board_id !== null && c.board_id !== undefined ? ' (board ' + esc(String(c.board_id)) + ')' : '')
                        + '</option>';
                });
                html += '<option value="__upload__" data-kind="upload">Upload custom .apj from this device…</option>';
                fwVersionSelect.innerHTML = html;
                fwVersionSelect.value = keep || '';
                if (fwVersionSelect.selectedIndex < 0) { fwVersionSelect.value = ''; }
                if (fwVersionInfo) {
                    var n = (d.previous || []).length;
                    fwVersionInfo.textContent = (n ? n + ' previous version' + (n > 1 ? 's' : '') + ' kept for reverting. '
                        : 'No previous versions saved yet (they are kept automatically when a newer build is downloaded). ')
                        + 'You can also upload your own .apj file.';
                }
            })
            .catch(function () {
                // Older server without this endpoint: keep the single "Latest" option.
            });
    }

    if (fwVersionSelect) {
        fwVersionSelect.addEventListener('change', function () {
            if (fwVersionSelect.value === '__upload__') {
                fwVersionSelect.value = '';
                if (fwCustomFile) { fwCustomFile.value = ''; fwCustomFile.click(); }
            }
        });
    }

    if (fwCustomFile) {
        fwCustomFile.addEventListener('change', function () {
            var f = fwCustomFile.files && fwCustomFile.files[0];
            if (!f) { return; }
            if (!/\.apj$/i.test(f.name)) { showToast('Please choose an ArduPilot .apj file.', true); return; }
            if (f.size > FW_UPLOAD_MAX) { showToast('That file is too large for an .apj (' + fwSizeText(f.size) + ').', true); return; }
            var reader = new FileReader();
            reader.onerror = function () { showToast('Could not read ' + f.name, true); };
            reader.onload = function () {
                var text = reader.result;
                try {
                    var j = JSON.parse(text);
                    if (!j || !j.image || j.board_id === undefined) { throw new Error('missing image/board_id'); }
                } catch (e) {
                    showToast('Not an ArduPilot .apj file: ' + f.name, true);
                    return;
                }
                if (fwVersionInfo) { fwVersionInfo.textContent = 'Uploading ' + f.name + ' (' + fwSizeText(f.size) + ')…'; }
                apiRequest('/api/flash/upload-apj', 'POST', { filename: f.name, content: text })
                    .then(function (r) {
                        showToast('✅ Uploaded ' + (r.firmware ? r.firmware.file : f.name) + ' — selected for install.');
                        loadFirmwareChoices(r.firmware ? r.firmware.path : '');
                    })
                    .catch(function (err) {
                        showToast('Upload failed: ' + err.message, true);
                        loadFirmwareChoices();
                    });
            };
            reader.readAsText(f);
        });
    }

    document.querySelectorAll('input[name="cubeType"]').forEach(function (r) {
        r.addEventListener('change', function () { loadFirmwareChoices(''); });
    });

    btnResetFlash.addEventListener('click', function () {
        isUpdateRunning = false;
        timerStarted = false;
        installationInProgress = false;
        offlineSinceMs = null;
        setUploadProgressVisible(true);
        if (rebootCountdownInterval) { clearInterval(rebootCountdownInterval); rebootCountdownInterval = null; }
        rebootTimerBox.style.display = 'none';
        var phReset = el('rebootStandbyPlaceholder');
        if (phReset) { phReset.style.display = 'flex'; }
        apiRequest('/api/flash/reset', 'POST').then(function () {
            showToast('Flash state reset.');
            flashTerminal.innerHTML = '<div class="log-line INFO">[System] Reset. Ready for new installation.</div>';
            flashProgressBar.style.width = '0%';
            flashPercentLabel.textContent = '0%';
            flashStageLabel.textContent = 'Status: Ready';
            pipelineStatusBadge.textContent = 'READY';
            ['stepCube','stepCopter','stepController','stepReboot'].forEach(function(id){
                el(id).className = 'step-item';
            });
        }).catch(function(){});
    });

    function startFlashPolling(installMode) {
        if (flashPollInterval) { clearInterval(flashPollInterval); }
        // Cube Only: only the autopilot reboots (about 30 s); the copter and controller
        // keep running, so the 6-minute Open Solo update countdown does not apply.
        var cubeOnly = (installMode === 'cube_only');
        var lastProgress = 0;
        var consecutiveErrors = 0;
        var countdownTriggered = false;

        flashPollInterval = setInterval(function () {
            apiRequest('/api/flash/status')
                .then(function (state) {
                    consecutiveErrors = 0;
                    lastProgress = state.progress || lastProgress;
                    updateFlashUI(state);
                    if (!state.is_flashing && state.status !== 'running') {
                        clearInterval(flashPollInterval);
                        btnStartFlash.disabled = false;
                        btnStartFlash.textContent = '🚀 START AUTOMATED INSTALLATION';
                        if (state.status === 'success' && cubeOnly) {
                            countdownTriggered = true;
                            isUpdateRunning = false;
                            pipelineStatusBadge.textContent = 'COMPLETE';
                            stepCube.className = 'step-item done';
                            showToast('🎉 Cube flashed! The autopilot reboots by itself in about 30 seconds — no copter restart needed.');
                        } else if (state.status === 'success' && !countdownTriggered) {
                            countdownTriggered = true;
                            showToast('🎉 Installation staged! Rebooting hardware...');
                            startRebootCountdown(360);
                        } else if (state.status === 'error') {
                            showToast('Installation error — check terminal logs.', true);
                        }
                    }
                }).catch(function () {
                    consecutiveErrors++;
                    // If connection drops while progress was at or above 75% (during Controller transfer or staging),
                    // the Controller/Copter has rebooted and severed the Wi-Fi AP. Transition cleanly to 100% and countdown!
                    if (!cubeOnly && lastProgress >= 75 && consecutiveErrors >= 2 && !countdownTriggered) {
                        countdownTriggered = true;
                        clearInterval(flashPollInterval);
                        btnStartFlash.disabled = false;
                        btnStartFlash.textContent = '🚀 START AUTOMATED INSTALLATION';

                        // Set UI to 100% complete
                        flashProgressBar.style.width = '100%';
                        flashPercentLabel.textContent = '100%';
                        flashStageLabel.textContent = 'Hardware Rebooting — Applying Updates (~6 min)';
                        pipelineStatusBadge.textContent = 'REBOOTING';
                        pipelineStatusBadge.className = 'badge badge-accent';

                        stepCube.className       = 'step-item done';
                        stepCopter.className     = 'step-item done';
                        stepController.className = 'step-item done';
                        stepReboot.className     = 'step-item active';

                        var timeStr = new Date().toTimeString().split(' ')[0];
                        flashTerminal.innerHTML += '<div class="log-line SUCCESS">[' + timeStr + '] 🎉 Hardware reboot initiated! Both Copter and Controller are applying Open Solo updates.</div>' +
                                                   '<div class="log-line INFO">[' + timeStr + '] 6-Minute Countdown started. Pair and calibrate after reboot finishes. The SoloLink Wi-Fi network must also be connected to continue.</div>';
                        flashTerminal.scrollTop = flashTerminal.scrollHeight;

                        showToast('🎉 Hardware rebooted! Open Solo update is applying (6 min timer started)...');
                        startRebootCountdown(360);
                    }
                });
        }, 1500);
    }

    function updateFlashUI(state) {
        flashProgressBar.style.width = state.progress + '%';
        flashPercentLabel.textContent = state.progress + '%';
        flashStageLabel.textContent = state.current_stage || 'In Progress';
        pipelineStatusBadge.textContent = state.status.toUpperCase();
        pipelineStatusBadge.className = 'badge' + (state.status === 'error' ? ' badge-danger' : ' badge-accent');

        var p = state.progress;
        stepCube.className       = 'step-item' + (p >= 40 ? ' done' : (p > 5 ? ' active' : ''));
        stepCopter.className     = 'step-item' + (p >= 67 ? ' done' : (p >= 42 ? ' active' : ''));
        stepController.className = 'step-item' + (p >= 90 ? ' done' : (p >= 68 ? ' active' : ''));
        stepReboot.className     = 'step-item' + (p === 100 ? ' done' : (p >= 95 ? ' active' : ''));

        if (state.logs && state.logs.length > 0) {
            flashTerminal.innerHTML = state.logs.map(function (l) {
                return '<div class="log-line ' + esc(l.level) + '">[' + esc(l.time) + '] ' + esc(l.message) + '</div>';
            }).join('');
            flashTerminal.scrollTop = flashTerminal.scrollHeight;
        }
    }

    function playHardwareAliveTone() {
        try {
            var AudioCtx = window.AudioContext || window.webkitAudioContext;
            if (!AudioCtx) return;
            var ctx = new AudioCtx();
            var osc = ctx.createOscillator();
            var gain = ctx.createGain();
            osc.type = 'sine';
            osc.frequency.setValueAtTime(587.33, ctx.currentTime);
            osc.frequency.exponentialRampToValueAtTime(880, ctx.currentTime + 0.15);
            gain.gain.setValueAtTime(0.25, ctx.currentTime);
            gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.6);
            osc.connect(gain);
            gain.connect(ctx.destination);
            osc.start();
            osc.stop(ctx.currentTime + 0.6);
        } catch (e) {
            console.log('Audio playback prevented or unsupported:', e);
        }
    }

    function flashScreenBorderGreen() {
        document.body.classList.remove('flash-alive-border');
        void document.body.offsetWidth;
        document.body.classList.add('flash-alive-border');
        setTimeout(function () {
            document.body.classList.remove('flash-alive-border');
        }, 5000);
    }

    function scrollToPairingInstructions() {
        var card = el('step2PairCard') || el('pairingCard') || document.querySelector('.pairing-guard-box') || el('btnPairController');
        if (card) {
            card.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }
    }

    function showTimerSuccessBox() {
        var box = el('rebootTimerBox');
        if (!box) { return; }
        var ph = el('rebootStandbyPlaceholder');
        if (ph) { ph.style.display = 'none'; }
        box.style.display = 'flex';
        Array.prototype.forEach.call(box.children, function (c) {
            if (c.id !== 'rebootSuccessAlert') { c.style.display = 'none'; }
        });
        var s = el('rebootSuccessAlert');
        if (!s) {
            s = document.createElement('div');
            s.id = 'rebootSuccessAlert';
            s.style.cssText = 'background: rgba(16,185,129,0.25); border: 2px solid #10b981; color: #6ee7b7; padding: 10px 14px; border-radius: 8px; font-size: 13px; font-weight: 700; text-align: center; width: 100%; box-sizing: border-box;';
            box.appendChild(s);
        }
        s.textContent = '🎉 Success! Copter link restored. Continue to Step 2: Pairing and Calibration.';
        s.style.display = 'block';
    }

    function resetTimerSuccessBox() {
        var box = el('rebootTimerBox');
        if (!box) { return; }
        Array.prototype.forEach.call(box.children, function (c) {
            c.style.display = (c.id === 'rebootSuccessAlert') ? 'none' : '';
        });
    }

    function onCopterHardwareAlive() {
        if (rebootPingInterval) {
            clearInterval(rebootPingInterval);
            rebootPingInterval = null;
        }
        // Explicitly clear the 6-minute countdown clock interval
        if (rebootCountdownInterval) {
            clearInterval(rebootCountdownInterval);
            rebootCountdownInterval = null;
        }
        timerStarted = false;
        try { localStorage.removeItem('solo_reboot_until'); localStorage.removeItem('solo_reboot_start'); } catch (e) {}

        var warningAlert = el('rebootWarningAlert');
        if (warningAlert) {
            warningAlert.style.transition = 'all 0.4s ease';
            warningAlert.style.background = 'rgba(16, 185, 129, 0.25)';
            warningAlert.style.borderColor = '#10b981';
            warningAlert.style.color = '#6ee7b7';
            warningAlert.textContent = 'Update Complete! Software pairing mode is currently active. Hold A+B on your Artoo Controller to finalize link.';
        }

        var rebootBar = el('rebootProgressBar');
        if (rebootBar) {
            rebootBar.style.width = '100%';
        }

        // Replace the countdown digits with a prominent green success alert.
        showTimerSuccessBox();

        if (flashProgressBar) { flashProgressBar.style.width = '100%'; }
        if (flashPercentLabel) { flashPercentLabel.textContent = '100%'; }
        if (flashStageLabel) { flashStageLabel.textContent = 'Copter is alive! Update process complete.'; }
        if (pipelineStatusBadge) {
            pipelineStatusBadge.textContent = 'ONLINE & READY';
            pipelineStatusBadge.className = 'badge badge-accent';
        }
        if (stepReboot) {
            stepReboot.className = 'step-item done';
        }

        if (flashTerminal) {
            var timeStr = new Date().toTimeString().split(' ')[0];
            flashTerminal.innerHTML += '<div class="log-line SUCCESS">[' + timeStr + '] 🎉 [Wi-Fi] Copter is alive! Update process complete.</div>' +
                                       '<div class="log-line INFO">[' + timeStr + '] Advancing automatically to Step 2: Pairing & Calibrations.</div>';
            flashTerminal.scrollTop = flashTerminal.scrollHeight;
        }

        playHardwareAliveTone();
        flashScreenBorderGreen();
        scrollToPairingInstructions();

        // Automatically proceed to calibrations view
        var calibTarget = el('step3CalibCard') || document.querySelector('.pos-steps-grid') || el('step2PairCard');
        if (calibTarget) {
            calibTarget.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }
        showToast('🎉 Copter is alive! Update process complete — proceed to Step 2: Pairing & Calibrations.');
    }

    function startRebootCountdown(seconds) {
        if (timerStarted) {
            // Single execution safety lock: block duplicate trigger or countdown reset
            return;
        }
        timerStarted = true;
        isUpdateRunning = true;
        previousCopterOnline = false;
        countdownStartTime = Date.now();
        // Stage 1 upload complete + reboots issued: arm the network-signal gate
        installationInProgress = true;
        offlineSinceMs = null;
        setUploadProgressVisible(false);
        resetTimerSuccessBox();
        var phCount = el('rebootStandbyPlaceholder');
        if (phCount) { phCount.style.display = 'none'; }
        rebootTimerBox.style.display = 'flex';

        var warningAlert = el('rebootWarningAlert');
        if (warningAlert) {
            warningAlert.style.background = 'rgba(239, 68, 68, 0.2)';
            warningAlert.style.borderColor = '#ef4444';
            warningAlert.style.color = '#fca5a5';
            warningAlert.textContent = '⚠️ DO NOT POWER OFF THE DRONE OR CONTROLLER. Firmware installation is executing internally.';
        }

        var total = seconds || 360;
        var remaining = total;
        try {
            localStorage.setItem('solo_reboot_until', Date.now() + (total * 1000));
            localStorage.setItem('solo_reboot_start', countdownStartTime);
        } catch (e) {}
        if (rebootCountdownInterval) { clearInterval(rebootCountdownInterval); }
        if (rebootPingInterval) { clearInterval(rebootPingInterval); }

        rebootCountdownInterval = setInterval(function () {
            var m = Math.floor(remaining / 60);
            var s = remaining % 60;
            if (rebootCountdown) {
                rebootCountdown.textContent = (m < 10 ? '0' : '') + m + ':' + (s < 10 ? '0' : '') + s;
                rebootCountdown.style.color = 'var(--accent)';
            }
            var rebootBar = el('rebootProgressBar');
            if (rebootBar) {
                var pct = Math.min(100, Math.round(((total - remaining) / total) * 100));
                rebootBar.style.width = pct + '%';
                rebootBar.style.background = '#ef4444';
            }
            if (--remaining < 0) {
                clearInterval(rebootCountdownInterval);
                rebootCountdownInterval = null;
                timerStarted = false;
                try { localStorage.removeItem('solo_reboot_until'); localStorage.removeItem('solo_reboot_start'); } catch (e) {}
                showToast('Hardware should be online. Pair, then calibrate. The SoloLink Wi-Fi network must also be connected to continue.');
            }
        }, 1000);

        // Active uncached status polling: wait initial 45 seconds, then poll every 3 seconds with AbortController 2500ms timeout
        rebootPingInterval = setInterval(function () {
            var elapsed = Date.now() - countdownStartTime;
            if (elapsed < 45000) {
                return; // Initial 45-second reboot delay
            }
            var controller = null;
            var signal = undefined;
            var timeoutId = null;
            if (typeof AbortController !== 'undefined') {
                controller = new AbortController();
                signal = controller.signal;
                timeoutId = setTimeout(function () {
                    try { controller.abort(); } catch (e) {}
                }, 2500);
            }
            fetch('/api/status?t=' + Date.now(), { cache: 'no-store', signal: signal })
                .then(function (res) {
                    if (timeoutId) { clearTimeout(timeoutId); }
                    if (res.ok) { return res.json(); }
                    throw new Error('Offline');
                })
                .then(function (statusData) {
                    // Valid HTTP 200 response received: immediately stop timer & transition
                    if (rebootPingInterval) {
                        clearInterval(rebootPingInterval);
                        rebootPingInterval = null;
                    }
                    if (rebootCountdownInterval) {
                        clearInterval(rebootCountdownInterval);
                        rebootCountdownInterval = null;
                    }
                    timerStarted = false;
                    isUpdateRunning = false;
                    installationInProgress = false;
                    try { localStorage.removeItem('solo_reboot_until'); localStorage.removeItem('solo_reboot_start'); } catch (e) {}
                    onCopterHardwareAlive();
                })
                .catch(function () {
                    if (timeoutId) { clearTimeout(timeoutId); }
                });
        }, 3000);
    }

    btnClearLogs.addEventListener('click', function () {
        flashTerminal.innerHTML = '<div class="log-line INFO">[Logs Cleared]</div>';
    });

    // -------------------------------------------------------------------------
    // FIRMWARE UPDATE & APJ BUILDER CLIENT
    // -------------------------------------------------------------------------
    function openFwUpdateModal() {
        if (fwUpdateModal) {
            fwUpdateModal.style.display = 'flex';
        }
    }

    function closeFwUpdateModal() {
        if (fwUpdateModal) {
            fwUpdateModal.style.display = 'none';
        }
        if (fwUpdatePollInterval) {
            clearInterval(fwUpdatePollInterval);
            fwUpdatePollInterval = null;
        }
    }

    if (btnCloseFwUpdateModal) {
        btnCloseFwUpdateModal.addEventListener('click', closeFwUpdateModal);
    }
    if (btnFwModalClose) {
        btnFwModalClose.addEventListener('click', closeFwUpdateModal);
    }

    // Wi-Fi Network Scanner helper
    function scanWifiNetworks(selectEl, scanBtnEl, inputEl) {
        if (!selectEl) return;
        var origText = scanBtnEl ? scanBtnEl.textContent : '';
        if (scanBtnEl) {
            scanBtnEl.disabled = true;
            scanBtnEl.textContent = '⏳ Scanning (~4s, SoloLink blips)...';
        }
        selectEl.innerHTML = '<option value="">-- Sweeping 2.4 GHz channels (~4s)... --</option>';

        apiRequest('/api/wifi/scan')
            .then(function (data) {
                if (scanBtnEl) {
                    scanBtnEl.disabled = false;
                    scanBtnEl.textContent = origText || '🔄 Scan Networks';
                }
                var networks = Array.isArray(data) ? data.map(function (s) { return typeof s === 'string' ? { ssid: s } : s; }) : ((data && data.networks) ? data.networks : []);
                selectEl.innerHTML = '';

                if (networks.length === 0) {
                    var optEmpty = document.createElement('option');
                    optEmpty.value = '';
                    optEmpty.textContent = '-- No broadcast networks detected (Click Scan to retry) --';
                    selectEl.appendChild(optEmpty);
                } else {
                    var optPlaceholder = document.createElement('option');
                    optPlaceholder.value = '';
                    optPlaceholder.textContent = '-- Select detected network (' + networks.length + ' found) --';
                    selectEl.appendChild(optPlaceholder);

                    networks.forEach(function (net) {
                        var opt = document.createElement('option');
                        opt.value = net.ssid;
                        var sigText = (net.signal ? (net.signal + ' dBm') : (net.quality ? (net.quality + '%') : ''));
                        var secText = net.security || 'WPA2';
                        var chText = net.channel ? ('Ch ' + net.channel + ', ') : '';
                        opt.setAttribute('data-security', net.security || '');
                        opt.setAttribute('data-supported', net.supported === false ? '0' : '1');
                        if (net.note) opt.setAttribute('data-note', net.note);
                        opt.textContent = (net.supported === false ? '⚠️ ' : '📶 ') + net.ssid +
                            (sigText ? (' (' + chText + sigText + ', ' + secText + ')') : '') +
                            (net.supported === false ? ' — not supported' : '');
                        selectEl.appendChild(opt);
                    });
                }

                var optManual = document.createElement('option');
                optManual.value = '__manual__';
                optManual.textContent = '✏️ Enter custom / hidden SSID manually...';
                selectEl.appendChild(optManual);

                if (inputEl && inputEl.value) {
                    for (var i = 0; i < selectEl.options.length; i++) {
                        if (selectEl.options[i].value === inputEl.value) {
                            selectEl.selectedIndex = i;
                            break;
                        }
                    }
                }
            })
            .catch(function (err) {
                if (scanBtnEl) {
                    scanBtnEl.disabled = false;
                    scanBtnEl.textContent = origText || '🔄 Scan Networks';
                }
                selectEl.innerHTML = '<option value="">-- Scan failed: ' + esc(err.message || 'error') + ' --</option>';
                var optManual = document.createElement('option');
                optManual.value = '__manual__';
                optManual.textContent = '✏️ Enter custom / hidden SSID manually...';
                selectEl.appendChild(optManual);
            });
    }

    // Password visibility toggle helper
    function setupPasswordToggle(pwdInput, toggleBtn, chkBox) {
        if (!pwdInput) return;

        function setVisibility(show) {
            pwdInput.type = show ? 'text' : 'password';
            if (toggleBtn) {
                toggleBtn.textContent = show ? '🙈' : '👁️';
                toggleBtn.title = show ? 'Hide password' : 'Show password';
            }
            if (chkBox && chkBox.checked !== show) {
                chkBox.checked = show;
            }
        }

        if (toggleBtn) {
            toggleBtn.addEventListener('click', function (e) {
                e.preventDefault();
                var willShow = (pwdInput.type === 'password');
                setVisibility(willShow);
                pwdInput.focus();
            });
        }

        if (chkBox) {
            chkBox.addEventListener('change', function () {
                setVisibility(chkBox.checked);
            });
        }
    }

    function showFwWifiPrompt(errorMsg) {
        if (fwWifiPromptSection) fwWifiPromptSection.style.display = 'block';
        if (fwMainSection) fwMainSection.style.display = 'none';
        if (btnConnectFwWifi) {
            btnConnectFwWifi.disabled = false;
            btnConnectFwWifi.textContent = 'Connect & Check Updates ➔';
        }
        if (btnCancelFwWifi) btnCancelFwWifi.disabled = false;
        if (fwWifiStatusMsg) {
            if (errorMsg) {
                fwWifiStatusMsg.style.display = 'block';
                fwWifiStatusMsg.style.background = 'rgba(239, 68, 68, 0.2)';
                fwWifiStatusMsg.style.border = '1px solid #ef4444';
                fwWifiStatusMsg.style.color = '#fca5a5';
                fwWifiStatusMsg.textContent = errorMsg;
            } else {
                fwWifiStatusMsg.style.display = 'none';
            }
        }
        if (fwWifiSsid) setTimeout(function () { fwWifiSsid.focus(); }, 100);
    }

    function showFwMainSection() {
        if (fwWifiPromptSection) fwWifiPromptSection.style.display = 'none';
        if (fwMainSection) fwMainSection.style.display = 'block';
        runFwUpdateCheck();
    }

    function runFwUpdateCheck() {
        fwModalStatusBadge.innerHTML = '<span style="color: #94a3b8;">Checking...</span>';
        fwModalMessage.innerHTML = 'Contacting ArduPilot Custom Firmware Builder (custom.ardupilot.org)...';
        btnFwModalStart.style.display = 'none';
        btnFwModalForce.style.display = 'none';
        fwModalProgressArea.style.display = 'none';
        fwModalTerminalBox.style.display = 'none';

        apiRequest('/api/firmware/check')
            .then(function (data) {
                if (!data.success && (data.error || data.status === 'NO_INTERNET_CONNECTION')) {
                    fwModalStatusBadge.innerHTML = '<span style="color: #ef4444; font-weight:700;">CHECK FAILED</span>';
                    var errMsg = data.error || data.status || 'NO_INTERNET_CONNECTION';
                    fwModalMessage.innerHTML = '<strong>⚠️ Error checking updates:</strong> ' + esc(errMsg);
                    if (data.status === 'NO_INTERNET_CONNECTION' || errMsg === 'NO_INTERNET_CONNECTION' || errMsg.indexOf('urlopen') !== -1 || errMsg.indexOf('Temporary failure') !== -1 || errMsg.indexOf('unreachable') !== -1) {
                        showFwWifiPrompt('Internet Connection Required to Fetch Online Firmware Build Packages.');
                    }
                    return;
                }

                var localVer = data.local_version || '4.7.2';
                var onlineVer = data.online_version || '4.7.2';
                fwModalCurrentVer.textContent = 'ArduCopter ' + localVer;
                fwModalOnlineVer.textContent = 'ArduCopter ' + onlineVer;
                // The header shows what the Cube runs (from /api/status), not the stored files.

                if (data.update_available) {
                    fwModalStatusBadge.innerHTML = '<span style="color: #3b82f6; font-weight:700;">UPDATE AVAILABLE</span>';
                    fwModalMessage.innerHTML = '<strong>🎉 Newer stable release detected!</strong><br>'
                        + 'Latest stable release is <strong>ArduCopter ' + esc(onlineVer) + '</strong> (current storage: ' + esc(localVer) + ').<br>'
                        + 'Click below to compile tailored APJs for CubeSolo and Green Cube, embed parameter defaults, and update setup packages.';
                    btnFwModalStart.style.display = 'inline-block';
                    btnFwModalStart.textContent = '🚀 Download & Build Update (v' + esc(onlineVer) + ')';
                    btnFwModalForce.style.display = 'none';
                } else {
                    fwModalStatusBadge.innerHTML = '<span style="color: #10b981; font-weight:700;">UP TO DATE</span>';
                    fwModalMessage.innerHTML = '<strong>✅ Firmware storage is up to date!</strong><br>'
                        + 'ArduCopter <strong>' + esc(localVer) + '</strong> matches the latest official stable release from ArduPilot.<br>'
                        + 'Active APJ binaries and setup packages are current.';
                    btnFwModalStart.style.display = 'none';
                    btnFwModalForce.style.display = 'inline-block';
                    btnFwModalForce.textContent = '⚡ Force Rebuild APJs';
                }
            })
            .catch(function (err) {
                fwModalStatusBadge.innerHTML = '<span style="color: #ef4444; font-weight:700;">ERROR</span>';
                fwModalMessage.innerHTML = '<strong>⚠️ Network error:</strong> ' + esc(err.message || 'Could not reach server');
                showFwWifiPrompt('Network unreachable. Please connect Controller to your Phone Hotspot or Home Wi-Fi:');
            });
    }

    if (btnConnectFwWifi) {
        btnConnectFwWifi.addEventListener('click', function () {
            var ssid = (fwWifiSsid ? fwWifiSsid.value : '').trim();
            if (!ssid && fwWifiSelect && fwWifiSelect.value && fwWifiSelect.value !== '__manual__') {
                ssid = fwWifiSelect.value;
                if (fwWifiSsid) fwWifiSsid.value = ssid;
            }
            var pwd  = (fwWifiPassword ? fwWifiPassword.value : '').trim();
            if (!ssid) {
                if (fwWifiStatusMsg) {
                    fwWifiStatusMsg.style.display = 'block';
                    fwWifiStatusMsg.style.background = 'rgba(239, 68, 68, 0.2)';
                    fwWifiStatusMsg.style.border = '1px solid #ef4444';
                    fwWifiStatusMsg.style.color = '#fca5a5';
                    fwWifiStatusMsg.textContent = 'Please select or enter a Wi-Fi / Hotspot SSID.';
                }
                return;
            }

            btnConnectFwWifi.disabled = true;
            btnConnectFwWifi.textContent = 'Connecting (~10s)...';
            if (btnCancelFwWifi) btnCancelFwWifi.disabled = true;
            if (fwWifiStatusMsg) {
                fwWifiStatusMsg.style.display = 'block';
                fwWifiStatusMsg.style.background = 'rgba(59, 130, 246, 0.2)';
                fwWifiStatusMsg.style.border = '1px solid #3b82f6';
                fwWifiStatusMsg.style.color = '#93c5fd';
                fwWifiStatusMsg.textContent = 'Connecting to "' + ssid + '" (~10s)... SoloLink AP will briefly cycle.';
            }

            apiRequest('/api/wifi/connect', 'POST', { ssid: ssid, password: pwd })
                .then(function (res) {
                    showToast(res.message || 'Connecting to Home Wi-Fi... SoloLink will briefly cycle.');
                    var attempts = 0;
                    var maxAttempts = 35; // 35 * 2000ms = 70 seconds
                    var wifiPoll = setInterval(function () {
                        attempts++;

                        if (fwWifiStatusMsg) {
                            if (attempts <= 6) {
                                fwWifiStatusMsg.textContent = '🔄 Cycling SoloLink AP (~10s)... Please wait';
                            } else {
                                fwWifiStatusMsg.textContent = '🌐 Verifying link (' + attempts + '/' + maxAttempts + ')...';
                            }
                        }

                        apiRequest('/api/wifi/status')
                            .then(function (status) {
                                if (status.connection_state === 'failed' && status.failure) {
                                    clearInterval(wifiPoll);
                                    btnConnectFwWifi.disabled = false;
                                    btnConnectFwWifi.textContent = 'Connect & Check Updates ➔';
                                    if (btnCancelFwWifi) btnCancelFwWifi.disabled = false;
                                    showFwWifiPrompt(status.failure);
                                    return;
                                }
                                if (status.internet_connected || (status.ip_address && status.ip_address !== '10.1.1.1')) {
                                    clearInterval(wifiPoll);
                                    btnConnectFwWifi.disabled = false;
                                    btnConnectFwWifi.textContent = 'Connect & Check Updates ➔';
                                    if (btnCancelFwWifi) btnCancelFwWifi.disabled = false;
                                    showToast('🎉 Connected to ' + (status.connected_ssid || ssid) + '! Home IP: ' + status.ip_address);
                                    showFwMainSection();
                                } else if (attempts >= maxAttempts) {
                                    clearInterval(wifiPoll);
                                    btnConnectFwWifi.disabled = false;
                                    btnConnectFwWifi.textContent = 'Connect & Check Updates ➔';
                                    if (btnCancelFwWifi) btnCancelFwWifi.disabled = false;
                                    showToast('Wi-Fi association finished. Checking updates...');
                                    showFwMainSection();
                                }
                            })
                            .catch(function () {
                                // AP cycle disconnection is expected
                                if (attempts >= maxAttempts) {
                                    clearInterval(wifiPoll);
                                    btnConnectFwWifi.disabled = false;
                                    btnConnectFwWifi.textContent = 'Connect & Check Updates ➔';
                                    if (btnCancelFwWifi) btnCancelFwWifi.disabled = false;
                                    showToast('Wi-Fi association finished. Checking updates...');
                                    showFwMainSection();
                                }
                            });
                    }, 2000);
                })
                .catch(function (err) {
                    btnConnectFwWifi.disabled = false;
                    btnConnectFwWifi.textContent = 'Connect & Check Updates ➔';
                    if (btnCancelFwWifi) btnCancelFwWifi.disabled = false;
                    showFwWifiPrompt('Failed to connect: ' + err.message);
                });
        });
    }

    if (btnScanFwWifi) {
        btnScanFwWifi.addEventListener('click', function (e) {
            e.preventDefault();
            scanWifiNetworks(fwWifiSelect, btnScanFwWifi, fwWifiSsid);
        });
    }

    if (fwWifiSelect) {
        fwWifiSelect.addEventListener('change', function () {
            if (fwWifiSelect.value === '__manual__') {
                if (fwWifiSsid) { fwWifiSsid.value = ''; fwWifiSsid.focus(); }
            } else if (fwWifiSelect.value) {
                if (fwWifiSsid) fwWifiSsid.value = fwWifiSelect.value;
                if (fwWifiPassword) fwWifiPassword.focus();
            }
        });
    }

    setupPasswordToggle(fwWifiPassword, btnToggleFwWifiPwd, chkShowFwWifiPwd);

    if (btnCancelFwWifi) {
        btnCancelFwWifi.addEventListener('click', closeFwUpdateModal);
    }

    if (btnCheckUpdates) {
        btnCheckUpdates.addEventListener('click', function () {
            btnCheckUpdates.disabled = true;
            btnCheckUpdates.textContent = '⏳ Checking...';
            openFwUpdateModal();

            // Verify if Controller is already connected to home Wi-Fi / has internet
            apiRequest('/api/wifi/status')
                .then(function (wifi) {
                    btnCheckUpdates.disabled = false;
                    btnCheckUpdates.textContent = '🔄 Check for Updates';

                    // If Controller is online and not connected to home Wi-Fi / no internet, prompt for SSID & password
                    if (wifi.controller_online && !wifi.internet_connected && (!wifi.mode || wifi.mode.indexOf('Home') === -1)) {
                        showFwWifiPrompt();
                    } else if (wifi.internet_connected) {
                        showFwMainSection();
                    } else {
                        // Dev/PC testing mode or fallback
                        apiRequest('/api/firmware/check')
                            .then(function (res) {
                                if (res.success) {
                                    showFwMainSection();
                                } else {
                                    showFwWifiPrompt('Internet access is required to check for updates.');
                                }
                            })
                            .catch(function () {
                                showFwWifiPrompt('Internet access is required to check for updates.');
                            });
                    }
                })
                .catch(function () {
                    btnCheckUpdates.disabled = false;
                    btnCheckUpdates.textContent = '🔄 Check for Updates';
                    apiRequest('/api/firmware/check')
                        .then(function (res) {
                            if (res.success) {
                                showFwMainSection();
                            } else {
                                showFwWifiPrompt('Internet access is required to check for updates.');
                            }
                        })
                        .catch(function () {
                            showFwWifiPrompt('Internet access is required to check for updates.');
                        });
                });
        });
    }

    function triggerFwUpdateRoutine(force) {
        if (isVehicleArmed) {
            showToast('⚠️ SAFETY LOCKOUT: Cannot run firmware update while vehicle is ARMED!', true);
            return;
        }

        btnFwModalStart.disabled = true;
        btnFwModalForce.disabled = true;
        btnFwModalClose.disabled = true;

        fwModalProgressArea.style.display = 'block';
        fwModalProgressBar.style.width = '5%';
        fwModalPercentLabel.textContent = '5%';
        fwModalStageLabel.textContent = 'Submitting build request to ArduPilot...';

        fwModalTerminalBox.style.display = 'block';
        fwModalTerminal.innerHTML = '<div class="log-line INFO">[System] Starting ArduPilot APJ Builder routine (force=' + force + ')...</div>';

        apiRequest('/api/firmware/update', 'POST', { force: !!force })
            .then(function (res) {
                showToast('🚀 APJ build and update routine started!');
                startFwUpdatePolling();
            })
            .catch(function (err) {
                btnFwModalStart.disabled = false;
                btnFwModalForce.disabled = false;
                btnFwModalClose.disabled = false;
                showToast('Update start failed: ' + err.message, true);
                fwModalStageLabel.textContent = 'Error: ' + err.message;
            });
    }

    if (btnFwModalStart) {
        btnFwModalStart.addEventListener('click', function () {
            triggerFwUpdateRoutine(false);
        });
    }

    if (btnFwModalForce) {
        btnFwModalForce.addEventListener('click', function () {
            if (confirm('Force rebuild and download APJ files from ArduPilot custom builder server?')) {
                triggerFwUpdateRoutine(true);
            }
        });
    }

    function startFwUpdatePolling() {
        if (fwUpdatePollInterval) { clearInterval(fwUpdatePollInterval); }

        fwUpdatePollInterval = setInterval(function () {
            apiRequest('/api/firmware/status')
                .then(function (st) {
                    var pct = st.progress || 0;
                    fwModalProgressBar.style.width = pct + '%';
                    fwModalPercentLabel.textContent = pct + '%';
                    fwModalStageLabel.textContent = st.stage || st.message || 'Building...';

                    if (st.logs && st.logs.length > 0) {
                        fwModalTerminal.innerHTML = st.logs.map(function (line) {
                            return '<div class="log-line INFO">' + esc(line) + '</div>';
                        }).join('');
                        fwModalTerminal.scrollTop = fwModalTerminal.scrollHeight;
                    }

                    if (!st.is_running) {
                        clearInterval(fwUpdatePollInterval);
                        fwUpdatePollInterval = null;
                        btnFwModalClose.disabled = false;
                        btnFwModalForce.disabled = false;
                        btnFwModalStart.disabled = false;

                        if (st.status === 'update_ready' || st.status === 'up_to_date') {
                            showToast('🎉 Firmware update completed! ArduCopter v' + st.local_version + ' is active.');
                            fwModalStatusBadge.innerHTML = '<span style="color: #10b981; font-weight:700;">UPDATE READY</span>';
                            fwModalMessage.innerHTML = '<strong>🎉 Firmware update successful!</strong><br>'
                                + 'ArduCopter <strong>v' + esc(st.local_version) + '</strong> has been built, default parameters embedded, and setup files updated.';

                            if (fwActiveBadge) {
                                fwActiveBadge.textContent = 'ArduCopter ' + st.local_version;
                            }
                            if (radioDescSolo) {
                                radioDescSolo.textContent = 'arducopter_CubeSolo_' + st.local_version + '.apj — ArduCopter ' + st.local_version;
                            }
                            if (radioDescGreen) {
                                radioDescGreen.textContent = 'arducopter_CubeGreen_' + st.local_version + '.apj — ArduCopter ' + st.local_version;
                            }
                            loadFirmwareChoices('');
                        } else if (st.status === 'error') {
                            showToast('Firmware update failed: ' + st.message, true);
                            fwModalStatusBadge.innerHTML = '<span style="color: #ef4444; font-weight:700;">BUILD ERROR</span>';
                            fwModalMessage.innerHTML = '<strong>⚠️ Build routine failed:</strong> ' + esc(st.message);
                        }
                    }
                })
                .catch(function (e) {
                    // Ignored temporary poll blip
                });
        }, 1500);
    }

    // =========================================================================
    // Calibration Manager (Independent IMU & Compass Calibrations)
    // =========================================================================
    var btnStartImuCalib       = el('btnStartImuCalib');
    var btnCancelImuCalib      = el('btnCancelImuCalib');
    var btnRebootPixhawk       = el('btnRebootPixhawk');
    var imuPromptBox           = el('imuPromptBox');
    var imuPromptText          = el('imuPromptText');
    var btnContinueImuCalib    = el('btnContinueImuCalib');
    var imuStatusBadge         = el('imuStatusBadge');
    var imuProgressBar         = el('imuProgressBar');
    var imuStageLabel          = el('imuStageLabel');
    var imuPercentLabel        = el('imuPercentLabel');
    var imuRebootBanner        = el('imuRebootBanner');

    var btnStartCompassCalib   = el('btnStartCompassCalib');
    var btnCancelCompassCalib  = el('btnCancelCompassCalib');
    var compassStatusBadge     = el('compassStatusBadge');
    var compassProgressBar     = el('compassProgressBar');
    var compassStageLabel      = el('compassStageLabel');
    var compassPercentLabel    = el('compassPercentLabel');
    var c1StatusBadge          = el('c1StatusBadge');
    var c1ProgressBar          = el('c1ProgressBar');
    var c1PercentLabel         = el('c1PercentLabel');
    var c1FitnessLabel         = el('c1FitnessLabel');
    var c2StatusBadge          = el('c2StatusBadge');
    var c2ProgressBar          = el('c2ProgressBar');
    var c2PercentLabel         = el('c2PercentLabel');
    var c2FitnessLabel         = el('c2FitnessLabel');
    var compassDoneBanner      = el('compassDoneBanner');

    var calibTerminal          = el('calibTerminal');
    var btnClearCalibLogs      = el('btnClearCalibLogs');
    var calibPollInterval      = null;

    // --- Compass result (offsets are autosaved by the autopilot; backend reports compass_saved) ---
    var compassSavedOK    = false;   // backend confirmed offsets written

    var posNames = {
        1: "Accurately Level",
        2: "Left Side",
        3: "Right Side",
        4: "Nose Down",
        5: "Nose Up",
        6: "On Back"
    };

    function updateCalibUI(state) {
        var isRunning = !!state.is_running;
        var st = state.status || 'ready';
        var step = state.step || '';
        var p = state.progress || 0;
        var positionsDone = state.positions_done || [];
        var posIndex = state.pos_index || 0;
        var posName = state.pos_name || posNames[posIndex] || '';

        // --- STEP 3: 6-Axis Accelerometer & IMU Calibration UI ---
        if (step === 'imu' || st.indexOf('imu') !== -1) {
            if (imuProgressBar) imuProgressBar.style.width = p + '%';
            if (imuPercentLabel) imuPercentLabel.textContent = p + '%';
            if (imuStageLabel) imuStageLabel.textContent = state.message || (p >= 100 ? '6-Axis IMU Calibration Complete' : 'Calibrating...');

            if (imuStatusBadge) {
                if (st === 'imu_waiting') {
                    imuStatusBadge.textContent = 'WAITING POS ' + posIndex + '/6';
                    imuStatusBadge.className = 'badge badge-accent';
                } else if (st === 'imu_sampling') {
                    imuStatusBadge.textContent = 'SAMPLING POS ' + posIndex + '/6';
                    imuStatusBadge.className = 'badge badge-accent';
                } else if (st === 'rebooting' || st === 'imu_rebooting') {
                    imuStatusBadge.textContent = 'AUTO-REBOOTING';
                    imuStatusBadge.className = 'badge badge-accent';
                } else if (st === 'imu_completed') {
                    imuStatusBadge.textContent = 'COMPLETED';
                    imuStatusBadge.className = 'badge badge-success';
                } else if (st === 'error') {
                    imuStatusBadge.textContent = 'ERROR';
                    imuStatusBadge.className = 'badge badge-danger';
                } else {
                    imuStatusBadge.textContent = 'READY';
                    imuStatusBadge.className = 'badge';
                }
            }

            // Update 6 position orientation cards
            for (var i = 1; i <= 6; i++) {
                var card = el('posCard' + i);
                var badge = el('posBadge' + i);
                if (!card || !badge) continue;

                if (positionsDone.indexOf(i) !== -1) {
                    card.className = 'pos-step-card done-pos';
                    badge.className = 'badge pos-badge badge-success';
                    badge.textContent = 'Done ✅';
                } else if (step === 'imu' && posIndex === i && isRunning) {
                    card.className = 'pos-step-card active-pos';
                    if (st === 'imu_sampling') {
                        badge.className = 'badge pos-badge badge-accent';
                        badge.textContent = 'Sampling 🔄';
                    } else {
                        badge.className = 'badge pos-badge badge-accent';
                        badge.textContent = 'Current ⏳ (Hold Still)';
                    }
                } else {
                    card.className = 'pos-step-card';
                    badge.className = 'badge pos-badge';
                    badge.textContent = 'Pos ' + i + ': Pending';
                }
            }

            // Update active position guidance prompt banner
            if (imuPromptBox) {
                if (isRunning && step === 'imu' && (st === 'imu_waiting' || st === 'imu_sampling')) {
                    imuPromptBox.style.display = 'flex';
                    var curName = posNames[posIndex] || posName || ('Position ' + posIndex);
                    if (st === 'imu_waiting') {
                        if (imuPromptText) {
                            imuPromptText.textContent = 'Step ' + posIndex + '/6: Place vehicle ' + curName + ', hold completely still, then click Continue.';
                        }
                        if (btnContinueImuCalib) {
                            btnContinueImuCalib.disabled = false;
                            btnContinueImuCalib.textContent = '➡️ Continue (' + curName + ')';
                        }
                    } else if (st === 'imu_sampling') {
                        if (imuPromptText) {
                            imuPromptText.textContent = 'Sampling ' + curName + '... Keep vehicle completely motionless!';
                        }
                        if (btnContinueImuCalib) {
                            btnContinueImuCalib.disabled = true;
                            btnContinueImuCalib.textContent = '⏳ Sampling Sensors...';
                        }
                    }
                } else {
                    imuPromptBox.style.display = 'none';
                }
            }

            if (imuRebootBanner) {
                imuRebootBanner.style.display = 'none'; // autopilot now reboots automatically after IMU calibration
            }

            if (btnStartImuCalib) {
                if (isRunning) {
                    btnStartImuCalib.disabled = true;
                    btnStartImuCalib.textContent = (st === 'rebooting' || st === 'imu_rebooting') ? '✅ Calibrated - Auto-Rebooting Autopilot...' : '⏳ Calibrating Position ' + (posIndex || 1) + '/6...';
                    if (btnCancelImuCalib) btnCancelImuCalib.style.display = 'inline-block';
                } else {
                    btnStartImuCalib.disabled = false;
                    btnStartImuCalib.textContent = '▶ Start 6-Axis Calibration';
                    if (btnCancelImuCalib) btnCancelImuCalib.style.display = 'none';
                }
            }
        }

        // --- Reboot Pixhawk UI ---
        if (step === 'reboot' || st.indexOf('reboot') !== -1) {
            if (btnRebootPixhawk) {
                if (isRunning) {
                    btnRebootPixhawk.disabled = true;
                    btnRebootPixhawk.textContent = '🔄 Pixhawk Rebooting...';
                } else {
                    btnRebootPixhawk.disabled = false;
                    btnRebootPixhawk.textContent = '🔄 Reboot Autopilot (Pixhawk)';
                }
            }
            if (imuStageLabel && isRunning && st !== 'imu_rebooting') {
                imuStageLabel.textContent = 'Autopilot rebooting... waiting for heartbeats';
            }
        }

        // --- STEP 4: Compass Calibration UI ---
        if (step === 'compass' || st.indexOf('compass') !== -1) {
            // Offsets are saved by the autopilot (autosave) and confirmed by the backend.
            compassSavedOK = !!state.compass_saved;
            if (compassProgressBar) compassProgressBar.style.width = p + '%';
            if (compassPercentLabel) compassPercentLabel.textContent = p + '%';
            if (compassStageLabel) compassStageLabel.textContent = state.message || (p >= 100 ? 'Calibration Complete' : 'Rotate vehicle in sphere...');

            if (compassStatusBadge) {
                if (compassSavedOK && st !== 'error') {
                    compassStatusBadge.textContent = 'Compass Calibrated Successfully';
                    compassStatusBadge.className = 'badge badge-success';
                } else if (st === 'compass_running') {
                    compassStatusBadge.textContent = 'ROTATING';
                    compassStatusBadge.className = 'badge badge-accent';
                } else if (st === 'compass_reboot_countdown') {
                    compassStatusBadge.textContent = 'AUTO-REBOOT';
                    compassStatusBadge.className = 'badge badge-accent';
                } else if (st === 'rebooting') {
                    compassStatusBadge.textContent = 'REBOOTING';
                    compassStatusBadge.className = 'badge badge-accent';
                } else if (st === 'compass_completed') {
                    compassStatusBadge.textContent = 'FLIGHT READY';
                    compassStatusBadge.className = 'badge badge-success';
                } else if (st === 'error') {
                    compassStatusBadge.textContent = 'ERROR';
                    compassStatusBadge.className = 'badge badge-danger';
                } else {
                    compassStatusBadge.textContent = 'READY';
                    compassStatusBadge.className = 'badge';
                }
            }

            // Dual Compass Gauges
            var cp = state.compass_progress || {};
            var cf = state.compass_fitness || {};

            // Compass 1 (External)
            var c1Val = cp[0] !== undefined ? cp[0] : (cp['0'] !== undefined ? cp['0'] : 0);
            if (c1ProgressBar) c1ProgressBar.style.width = c1Val + '%';
            if (c1PercentLabel) c1PercentLabel.textContent = c1Val + '%';
            if (c1StatusBadge) {
                c1StatusBadge.textContent = c1Val >= 100 ? 'DONE' : (c1Val > 0 ? 'CALIBRATING' : 'WAITING');
                c1StatusBadge.className = 'badge' + (c1Val >= 100 ? ' badge-success' : (c1Val > 0 ? ' badge-accent' : ''));
            }
            if (c1FitnessLabel) {
                var fit1 = cf[0] !== undefined ? cf[0] : cf['0'];
                c1FitnessLabel.textContent = fit1 !== undefined ? ('Fitness: ' + fit1 + (fit1 < 0.5 ? ' (Good)' : '')) : 'Fitness: --';
            }

            // Compass 2 (Internal)
            var c2Val = cp[1] !== undefined ? cp[1] : (cp['1'] !== undefined ? cp['1'] : 0);
            if (c2ProgressBar) c2ProgressBar.style.width = c2Val + '%';
            if (c2PercentLabel) c2PercentLabel.textContent = c2Val + '%';
            if (c2StatusBadge) {
                c2StatusBadge.textContent = c2Val >= 100 ? 'DONE' : (c2Val > 0 ? 'CALIBRATING' : 'WAITING');
                c2StatusBadge.className = 'badge' + (c2Val >= 100 ? ' badge-success' : (c2Val > 0 ? ' badge-accent' : ''));
            }
            if (c2FitnessLabel) {
                var fit2 = cf[1] !== undefined ? cf[1] : cf['1'];
                c2FitnessLabel.textContent = fit2 !== undefined ? ('Fitness: ' + fit2 + (fit2 < 0.5 ? ' (Good)' : '')) : 'Fitness: --';
            }

            if (compassDoneBanner) {
                compassDoneBanner.style.display = (st === 'compass_completed' || compassSavedOK) ? 'flex' : 'none';
            }

            // Rotation guidance banner (shown for the whole compass run)
            var gpsNotice = document.getElementById('compassGpsNotice');
            if (!gpsNotice && calibTerminal && calibTerminal.parentNode) {
                gpsNotice = document.createElement('div');
                gpsNotice.id = 'compassGpsNotice';
                gpsNotice.className = 'calib-callout-banner';
                gpsNotice.style.cssText = 'display:none; margin-bottom:8px; padding:10px 12px; border-radius:6px; font-size:12px; background:rgba(234,179,8,0.12); border:1px solid #eab308;';
                gpsNotice.textContent = '🧭 Keep rotating slowly through every orientation (nose up/down, both sides, upside down) away from metal. If a compass does not fit, the autopilot restarts it automatically - just keep rotating.';
                calibTerminal.parentNode.insertBefore(gpsNotice, calibTerminal);
            }
            if (gpsNotice) {
                gpsNotice.style.display = (isRunning && st === 'compass_running') ? 'block' : 'none';
            }

            if (btnStartCompassCalib) {
                if (isRunning && !compassSavedOK) {
                    btnStartCompassCalib.disabled = true;
                    if (st === 'compass_reboot_countdown') {
                        btnStartCompassCalib.textContent = '⏳ Auto-Rebooting Autopilot in 3s...';
                    } else if (st === 'rebooting') {
                        btnStartCompassCalib.textContent = '🔄 Autopilot Rebooting...';
                    } else {
                        btnStartCompassCalib.textContent = '🧭 Calibrating... Perform Dance';
                    }
                    if (btnCancelCompassCalib) btnCancelCompassCalib.style.display = 'inline-block';
                } else if (isRunning && compassSavedOK) {
                    // Offsets saved; autopilot is finishing its reboot in the background
                    btnStartCompassCalib.disabled = true;
                    btnStartCompassCalib.textContent = '✅ Compass Saved - Autopilot Restarting...';
                    if (btnCancelCompassCalib) btnCancelCompassCalib.style.display = 'none';
                } else {
                    btnStartCompassCalib.disabled = false;
                    btnStartCompassCalib.textContent = '▶ Start Compass Calibration';
                    if (btnCancelCompassCalib) btnCancelCompassCalib.style.display = 'none';
                }
            }
        }

        // --- Terminal Logs Output ---
        if (state.logs && state.logs.length > 0 && calibTerminal) {
            calibTerminal.innerHTML = state.logs.map(function (l) {
                return '<div class="log-line ' + esc(l.level) + '">[' + esc(l.time) + '] ' + esc(l.message) + '</div>';
            }).join('');
            calibTerminal.scrollTop = calibTerminal.scrollHeight;
        }
    }

    function startCalibPolling() {
        if (calibPollInterval) clearInterval(calibPollInterval);
        calibPollInterval = setInterval(function () {
            apiRequest('/api/calib/status')
                .then(function (state) {
                    updateCalibUI(state);
                    if (!state.is_running) {
                        clearInterval(calibPollInterval);
                        if (state.status === 'imu_completed') {
                            showToast('✅ IMU calibration complete & autopilot rebooted! You may now calibrate the compass.');
                        } else if (state.status === 'reboot_completed') {
                            showToast('🔄 Autopilot reboot complete! You may now calibrate compass.');
                        } else if (state.status === 'compass_completed') {
                            showToast('🎉 Compass calibration & autopilot reboot complete! Solo is ready to fly.');
                        } else if (state.status === 'error') {
                            showToast('Calibration error: ' + (state.message || 'Check terminal logs'), true);
                        }
                    }
                }).catch(function () {});
        }, 1200);
    }

    // Step 3: IMU Calibration Button Handlers
    if (btnStartImuCalib) {
        btnStartImuCalib.addEventListener('click', function () {
            btnStartImuCalib.disabled = true;
            btnStartImuCalib.textContent = '⏳ Starting 6-Axis Cal...';
            if (btnCancelImuCalib) btnCancelImuCalib.style.display = 'inline-block';
            if (imuRebootBanner) imuRebootBanner.style.display = 'none';

            apiRequest('/api/calib/imu/start', 'POST', {})
                .then(function (res) {
                    showToast('📐 6-Axis calibration started! Place Solo Level and click Continue.');
                    startCalibPolling();
                }).catch(function (err) {
                    btnStartImuCalib.disabled = false;
                    btnStartImuCalib.textContent = '▶ Start 6-Axis Calibration';
                    if (btnCancelImuCalib) btnCancelImuCalib.style.display = 'none';
                    showToast('Failed to start calibration: ' + err.message, true);
                });
        });
    }

    if (btnContinueImuCalib) {
        btnContinueImuCalib.addEventListener('click', function () {
            btnContinueImuCalib.disabled = true;
            btnContinueImuCalib.textContent = '⏳ Sampling Sensors...';
            apiRequest('/api/calib/imu/continue', 'POST', {})
                .then(function (res) {
                    showToast('Position confirmed! Hold vehicle completely still.');
                }).catch(function (err) {
                    btnContinueImuCalib.disabled = false;
                    showToast('Continue error: ' + err.message, true);
                });
        });
    }

    if (btnCancelImuCalib) {
        btnCancelImuCalib.addEventListener('click', function () {
            apiRequest('/api/calib/cancel', 'POST', {})
                .then(function (res) {
                    showToast('Cancellation requested.');
                }).catch(function (err) {
                    showToast('Cancel error: ' + err.message, true);
                });
        });
    }

    if (btnRebootPixhawk) {
        btnRebootPixhawk.addEventListener('click', function () {
            if (!confirm('Reboot the Pixhawk autopilot now? Wait ~20 seconds for sensors to reinitialize.')) { return; }
            btnRebootPixhawk.disabled = true;
            btnRebootPixhawk.textContent = '🔄 Issuing Reboot...';
            apiRequest('/api/calib/reboot', 'POST', {})
                .then(function (res) {
                    showToast('🔄 Autopilot reboot command sent. Waiting for heartbeats...');
                    startCalibPolling();
                }).catch(function (err) {
                    btnRebootPixhawk.disabled = false;
                    btnRebootPixhawk.textContent = '🔄 Reboot Autopilot (Pixhawk)';
                    showToast('Reboot error: ' + err.message, true);
                });
        });
    }

    // Step 4: Compass Calibration Button Handlers
    if (btnStartCompassCalib) {
        btnStartCompassCalib.addEventListener('click', function () {
            btnStartCompassCalib.disabled = true;
            compassSavedOK = false;
            btnStartCompassCalib.textContent = '⏳ Starting Compass Cal...';
            if (btnCancelCompassCalib) btnCancelCompassCalib.style.display = 'inline-block';
            if (compassDoneBanner) compassDoneBanner.style.display = 'none';

            apiRequest('/api/calib/compass/start', 'POST', { wait_completion: true })
                .then(function (res) {
                    showToast('🧭 Compass calibration active! Pick up Solo and perform the dance.');
                    startCalibPolling();
                }).catch(function (err) {
                    btnStartCompassCalib.disabled = false;
                    btnStartCompassCalib.textContent = '▶ Start Compass Calibration';
                    if (btnCancelCompassCalib) btnCancelCompassCalib.style.display = 'none';
                    showToast('Failed to start Compass calibration: ' + err.message, true);
                });
        });
    }

    if (btnCancelCompassCalib) {
        btnCancelCompassCalib.addEventListener('click', function () {
            apiRequest('/api/calib/cancel', 'POST', {})
                .then(function (res) {
                    showToast('Compass calibration cancel requested.');
                }).catch(function (err) {
                    showToast('Cancel error: ' + err.message, true);
                });
        });
    }

    if (btnClearCalibLogs) {
        btnClearCalibLogs.addEventListener('click', function () {
            if (calibTerminal) {
                calibTerminal.innerHTML = '<div class="log-line INFO">[Calibration Logs Cleared]</div>';
            }
        });
    }

    // =========================================================================
    // TAB 2: WI-FI & INTERNET
    // =========================================================================
    var STEP_ICONS = { ok: '✅', failed: '❌', running: '⏳', pending: '○', skipped: '–' };
    var STEP_COLORS = { ok: '#10b981', failed: '#ef4444', running: '#60a5fa', pending: '#64748b', skipped: '#64748b' };

    function setStatusVal(node, on, onText, offText, offColor) {
        if (!node) return;
        node.textContent = on ? onText : offText;
        node.style.color = on ? '#10b981' : (offColor || '#ef4444');
    }

    function renderWifiSteps(steps) {
        if (!wifiSteps) return;
        if (!steps || !steps.length) {
            wifiSteps.innerHTML = '<li style="color: #94a3b8;">No connection attempt yet.</li>';
            return;
        }
        var html = '';
        steps.forEach(function (st) {
            html += '<li style="color: ' + (STEP_COLORS[st.state] || '#94a3b8') + '; margin-bottom: 2px;">' +
                (STEP_ICONS[st.state] || '○') + ' ' + esc(st.label) +
                (st.detail ? ' <span style="color: #94a3b8;">— ' + esc(st.detail) + '</span>' : '') + '</li>';
        });
        wifiSteps.innerHTML = html;
    }

    function renderWifiProblem(status) {
        if (!wifiProblem) return;
        var text = status.failure || status.warning || '';
        if (!text) { wifiProblem.style.display = 'none'; return; }
        var isError = !!status.failure;
        wifiProblem.style.display = 'block';
        wifiProblem.style.background = isError ? 'rgba(239, 68, 68, 0.15)' : 'rgba(245, 158, 11, 0.15)';
        wifiProblem.style.border = '1px solid ' + (isError ? '#ef4444' : '#f59e0b');
        wifiProblem.style.color = isError ? '#fca5a5' : '#fde68a';
        wifiProblem.textContent = (isError ? '❌ ' : '⚠️ ') + text;
    }

    function renderWifiStatus(status) {
        if (!status) return;
        if (netStatusSsid) netStatusSsid.textContent = status.connected_ssid || 'SoloLink_AP';
        if (netStatusMode) netStatusMode.textContent = status.mode || 'Access Point';
        if (netStatusIp)   netStatusIp.textContent   = status.ip_address || '10.1.1.1';
        setStatusVal(netStatusInternet, status.internet_connected, '● ONLINE', '○ Offline');
        setStatusVal(netStatusFwd, status.ip_forwarding, '● Enabled', '○ Disabled', '#94a3b8');
        setStatusVal(netStatusCtrl, status.controller_online, '● Online', '○ Offline');
        if (netStatusCopterNet) {
            if (status.connection_state === 'connected') {
                setStatusVal(netStatusCopterNet, status.copter_internet, '● ONLINE', '○ No route');
            } else {
                netStatusCopterNet.textContent = '—';
                netStatusCopterNet.style.color = '#94a3b8';
            }
        }
        if (wifiModeBadge) wifiModeBadge.textContent = status.mode || 'AP Mode';
        if (chkBidirectional) chkBidirectional.checked = !!status.bidirectional_enabled;
        if (natStatusBadge) {
            natStatusBadge.textContent = status.bidirectional_enabled ? 'Active' : 'Disabled';
            natStatusBadge.style.color = status.bidirectional_enabled ? '#10b981' : '#94a3b8';
        }
        renderWifiSteps(status.steps);
        renderWifiProblem(status);
        if (wifiSetupLog) wifiSetupLog.textContent = status.setup_log || '(empty)';
        var ctrlIp = (status.ip_address && status.ip_address !== '10.1.1.1') ? status.ip_address : '<CONTROLLER_HOME_IP>';
        if (routeHelperCmd) routeHelperCmd.textContent = 'route add 10.1.1.0 mask 255.255.255.0 ' + ctrlIp;
    }

    function refreshWifiStatus() {
        apiRequest('/api/wifi/status')
            .then(function (status) {
                renderWifiStatus(status);
            }).catch(function (err) {
                showToast('Wi-Fi status error: ' + err.message, true);
            });
    }

    // Security of the network chosen in the scan list ('' when typed by hand).
    function selectedWifiSecurity(ssid) {
        if (!wifiSelect || wifiSelect.selectedIndex < 0) return { security: '', supported: true, note: '' };
        var opt = wifiSelect.options[wifiSelect.selectedIndex];
        if (!opt || opt.value !== ssid) return { security: '', supported: true, note: '' };
        return {
            security: opt.getAttribute('data-security') || '',
            supported: opt.getAttribute('data-supported') !== '0',
            note: opt.getAttribute('data-note') || ''
        };
    }

    function updateSecurityHint() {
        if (!wifiSecurityHint) return;
        var ssid = (wifiSsid ? wifiSsid.value : '').trim();
        var info = selectedWifiSecurity(ssid);
        var isOpen = info.security === 'Open';
        if (wifiPassword) {
            wifiPassword.disabled = isOpen;
            if (isOpen) wifiPassword.value = '';
        }
        if (!info.supported) {
            wifiSecurityHint.style.display = 'block';
            wifiSecurityHint.style.background = 'rgba(239, 68, 68, 0.15)';
            wifiSecurityHint.style.border = '1px solid #ef4444';
            wifiSecurityHint.style.color = '#fca5a5';
            wifiSecurityHint.textContent = '❌ ' + info.note;
        } else if (isOpen) {
            wifiSecurityHint.style.display = 'block';
            wifiSecurityHint.style.background = 'rgba(59, 130, 246, 0.15)';
            wifiSecurityHint.style.border = '1px solid #3b82f6';
            wifiSecurityHint.style.color = '#93c5fd';
            wifiSecurityHint.textContent = 'Open network: no password needed.';
        } else {
            wifiSecurityHint.style.display = 'none';
        }
    }

    function resetConnectButton() {
        btnConnectWifi.disabled = false;
        btnConnectWifi.textContent = '🌐 Connect to Home Wi-Fi';
    }

    function runningStepLabel(steps) {
        var label = '';
        (steps || []).forEach(function (st) { if (st.state === 'running') label = st.label; });
        return label;
    }

    btnRefreshWifiStatus.addEventListener('click', function () { refreshWifiStatus(); });

    btnConnectWifi.addEventListener('click', function () {
        if (isVehicleArmed) {
            var armedMsg = 'Not sent: the copter currently reports ARMED, and home Wi-Fi is locked while armed. ' +
                'If the motors are not running, check the Cockpit message list for an unexpected "MOTORS ARMED".';
            renderWifiProblem({ failure: armedMsg });
            showToast('⚠️ ' + armedMsg, true);
            return;
        }
        var ssid = (wifiSsid ? wifiSsid.value : '').trim();
        if (!ssid && wifiSelect && wifiSelect.value && wifiSelect.value !== '__manual__') {
            ssid = wifiSelect.value;
            if (wifiSsid) wifiSsid.value = ssid;
        }
        if (!ssid) { renderWifiProblem({ failure: 'Select or type your home Wi-Fi network name (SSID).' }); return; }
        var info = selectedWifiSecurity(ssid);
        if (!info.supported) { renderWifiProblem({ failure: info.note }); showToast('❌ ' + info.note, true); return; }
        var pwd = (wifiPassword && !wifiPassword.disabled) ? wifiPassword.value : '';
        if (info.security && info.security !== 'Open' && !pwd) {
            renderWifiProblem({ failure: '"' + ssid + '" is secured (' + info.security + '). Enter its password.' });
            return;
        }

        btnConnectWifi.disabled = true;
        btnConnectWifi.textContent = '🔄 Sending settings...';
        renderWifiSteps([]);
        renderWifiProblem({});
        apiRequest('/api/wifi/connect', 'POST', { ssid: ssid, password: pwd, security: info.security })
            .then(function (res) {
                showToast(res.message || 'Connecting... SoloLink will drop briefly.');
                var attempts = 0;
                var maxAttempts = 45;      // 45 x 2 s = 90 s
                var lostLink = 0;
                var pollTimer = setInterval(function () {
                    attempts++;
                    apiRequest('/api/wifi/status')
                        .then(function (status) {
                            lostLink = 0;
                            renderWifiStatus(status);
                            var stepLabel = runningStepLabel(status.steps);
                            btnConnectWifi.textContent = '⏳ ' + (stepLabel || 'Working') + '...';

                            if (status.connection_state === 'failed') {
                                clearInterval(pollTimer);
                                resetConnectButton();
                                showToast('❌ ' + (status.failure || 'Connection failed. See the progress list.'), true);
                                return;
                            }
                            if (status.connection_state === 'connected') {
                                clearInterval(pollTimer);
                                resetConnectButton();
                                if (status.copter_internet) {
                                    showToast('🎉 Connected to ' + status.connected_ssid + ' (' + status.ip_address + '). Solo has internet.');
                                } else {
                                    showToast('⚠️ ' + (status.warning || 'Controller connected, but Solo has no internet yet.'), true);
                                }
                                return;
                            }
                            if (attempts >= maxAttempts) {
                                clearInterval(pollTimer);
                                resetConnectButton();
                                showToast('Still not connected after 90 s. Check the progress list and setup log.', true);
                            }
                        })
                        .catch(function () {
                            // Expected while SoloLink is paused: this page is served by the copter over SoloLink.
                            lostLink++;
                            btnConnectWifi.textContent = '📡 SoloLink paused while the Controller joins (' + (lostLink * 2) + ' s)...';
                            if (attempts >= maxAttempts) {
                                clearInterval(pollTimer);
                                resetConnectButton();
                                showToast('Lost contact with Solo. Reconnect this device to SoloLink_..., then press Refresh Network Status.', true);
                            }
                        });
                }, 2000);
            }).catch(function (err) {
                // Refused before anything was sent to the Controller: say why, and keep it on screen.
                renderWifiProblem({ failure: 'Connect was not started: ' + err.message });
                showToast('❌ ' + err.message, true);
                resetConnectButton();
            });
    });

    if (btnScanWifi) {
        btnScanWifi.addEventListener('click', function (e) {
            e.preventDefault();
            scanWifiNetworks(wifiSelect, btnScanWifi, wifiSsid);
        });
    }

    if (wifiSelect) {
        wifiSelect.addEventListener('change', function () {
            if (wifiSelect.value === '__manual__') {
                if (wifiSsid) { wifiSsid.value = ''; wifiSsid.focus(); }
            } else if (wifiSelect.value) {
                if (wifiSsid) wifiSsid.value = wifiSelect.value;
                if (wifiPassword && !wifiPassword.disabled) wifiPassword.focus();
            }
            updateSecurityHint();
        });
    }
    if (wifiSsid) {
        wifiSsid.addEventListener('input', updateSecurityHint);
    }

    setupPasswordToggle(wifiPassword, btnToggleWifiPwd, chkShowWifiPwd);

    chkBidirectional.addEventListener('change', function () {
        var enable = chkBidirectional.checked;
        apiRequest('/api/wifi/bidirectional', 'POST', { enable: enable })
            .then(function (res) {
                showToast(res.message);
                setTimeout(refreshWifiStatus, 1500);
            }).catch(function (err) {
                if (err.message && err.message.indexOf('fetch') !== -1) {
                    showToast('Routing updated. Syncing status...');
                    setTimeout(refreshWifiStatus, 2000);
                } else {
                    showToast('Error: ' + err.message, true);
                    chkBidirectional.checked = !enable;
                }
            });
    });

    btnResetDefaultWifi.addEventListener('click', function () {
        if (!confirm('Reset Controller Wi-Fi to default factory SoloLink AP mode?')) { return; }
        apiRequest('/api/wifi/reset-default', 'POST')
            .then(function (res) {
                showToast(res.message);
                setTimeout(refreshWifiStatus, 3000);
            }).catch(function (err) {
                if (err.message && err.message.indexOf('fetch') !== -1) {
                    showToast('Wi-Fi reset command applied.');
                    setTimeout(refreshWifiStatus, 3000);
                } else {
                    showToast('Error: ' + err.message, true);
                }
            });
    });


    // =========================================================================
    // TAB 3: PARAMETERS & PRESETS
    // =========================================================================
    var cachedPresets = [];

    var PRESET_GROUPS = [
        {
            title: '🧭 GPS & Compass Presets',
            match: function (p) { return p.id === 'here_gps' || p.id === 'stock_compass' || p.category === 'Navigation'; }
        },
        {
            title: '🔋 Battery & Power Presets',
            match: function (p) { return p.id.indexOf('lipo') !== -1 || p.id.indexOf('liion') !== -1 || (p.category && p.category.indexOf('Battery') !== -1); }
        },
        {
            title: '🎣 Payload & Airframe Presets',
            match: function (p) { return p.id === 'bait_drop' || p.id === 'retract_gear' || p.category === 'Payload' || p.category === 'Airframe'; }
        },
        {
            title: '⚡ Flight Performance Presets',
            match: function (p) { return p.category === 'Flight Performance' || p.id.indexOf('speed') !== -1; }
        }
    ];

    function loadPresets() {
        apiRequest('/api/params/presets')
            .then(function (res) {
                cachedPresets = res.presets || [];
                renderPresets(cachedPresets);
            })
            .catch(function(){});
    }

    function renderPresets(presets) {
        var used = {};
        var html = '';

        PRESET_GROUPS.forEach(function (grp) {
            var groupPresets = presets.filter(function (p) {
                if (used[p.id]) { return false; }
                if (grp.match(p)) {
                    used[p.id] = true;
                    return true;
                }
                return false;
            });

            if (groupPresets.length === 0) { return; }

            html += '<div class="preset-group-block">'
                + '<div class="preset-group-header">'
                + '<span class="preset-group-title">' + esc(grp.title) + '</span>'
                + '<span class="preset-group-count">' + groupPresets.length + ' preset' + (groupPresets.length > 1 ? 's' : '') + '</span>'
                + '</div>'
                + '<div class="presets-grid">'
                + groupPresets.map(renderSinglePresetCard).join('')
                + '</div>'
                + '</div>';
        });

        var leftover = presets.filter(function (p) { return !used[p.id]; });
        if (leftover.length > 0) {
            html += '<div class="preset-group-block">'
                + '<div class="preset-group-header"><span class="preset-group-title">📋 Additional Presets</span></div>'
                + '<div class="presets-grid">'
                + leftover.map(renderSinglePresetCard).join('')
                + '</div>'
                + '</div>';
        }

        presetsGrid.innerHTML = html;
    }

    function renderSinglePresetCard(p) {
        var paramCount = Object.keys(p.params || {}).length;
        var paramTags = Object.keys(p.params || {}).map(function (k) {
            return '<span class="preset-param-tag"><code>' + esc(k) + '</code> = <b>' + esc(String(p.params[k])) + '</b></span>';
        }).join(' ');

        return '<div class="preset-card">'
            + '<div>'
            + '<span class="preset-badge">' + esc(p.category || 'Preset') + '</span>'
            + '<div class="preset-header">'
            + '<span class="preset-icon">' + (p.icon || '⚙️') + '</span>'
            + '<span class="preset-name">' + esc(p.name) + '</span>'
            + '</div>'
            + '<p class="preset-desc">' + esc(p.description) + '</p>'
            + '<div class="preset-params-list">'
            + '<div class="preset-params-heading">Parameters to change (' + paramCount + '):</div>'
            + '<div style="line-height: 1.8;">' + paramTags + '</div>'
            + '</div>'
            + '</div>'
            + '<button class="btn btn-secondary btn-sm" onclick="window.applyPreset(\'' + p.id + '\')">'
            + '⚡ Apply Preset (' + paramCount + ' params)</button>'
            + '</div>';
    }

    window.applyPreset = function (presetId) {
        var p = (cachedPresets || []).find(function (item) { return item.id === presetId; });
        var name = p ? p.name : presetId;
        var paramLines = '';
        if (p && p.params) {
            paramLines = '\n\nParameters that will be committed to the flight controller:\n'
                + Object.keys(p.params).map(function (k) {
                    return '  • ' + k + ' = ' + p.params[k];
                }).join('\n');
        }
        var confirmMsg = 'Apply the \'' + name + '\' preset directly to the Solo flight controller?' + paramLines;
        if (!confirm(confirmMsg)) { return; }

        showToast('Writing preset \'' + name + '\' to the Cube...');
        apiRequest('/api/params/apply-preset', 'POST', { preset_id: presetId })
            .then(function (res) {
                // The preset is already written and confirmed on the Cube: these values are
                // saved, so they must NOT be marked as unsaved edits for "Save Changes".
                if (res.params) {
                    Object.keys(res.params).forEach(function (k) {
                        delete modifiedParams[k];
                    });
                    updateSaveButtonState();
                }
                showToast(res.message);
                loadParams(paramSearchInput.value.trim());
            }).catch(function (err) {
                showToast('Failed: ' + err.message, true);
                // some parameters may have been saved before the failure: show the real values
                loadParams(paramSearchInput.value.trim());
            });
    };

    function updateSaveButtonState() {
        var count = Object.keys(modifiedParams).length;
        if (count > 0) {
            btnSaveAllParams.textContent = '💾 Save Changes (' + count + ' modified)';
            btnSaveAllParams.classList.remove('btn-secondary');
            btnSaveAllParams.classList.add('btn-primary');
        } else {
            btnSaveAllParams.textContent = '💾 Save Changes';
        }
    }

    function loadParams(searchQuery) {
        var url = searchQuery ? ('/api/params?q=' + encodeURIComponent(searchQuery)) : '/api/params';
        apiRequest(url)
            .then(function (res) {
                allParams = res.params || [];
                if (!searchQuery) {
                    fullParams = allParams;
                    allParams.forEach(function (p) { knownDesc[p.name] = p.description || ''; });
                }
                paramSearchCount.textContent = 'Showing ' + res.count + ' of ' + res.total + ' parameters';
                renderParamTable(allParams);
            }).catch(function (err) { showToast('Failed to load params: ' + err.message, true); });
    }

    function renderParamTable(params) {
        if (params.length === 0) {
            paramTableBody.innerHTML = '<tr><td colspan="3" style="text-align:center;padding:20px;color:#94a3b8;">No matching parameters found.</td></tr>';
            return;
        }
        paramTableBody.innerHTML = params.map(function (p) {
            var isMod = modifiedParams.hasOwnProperty(p.name);
            var curVal = isMod ? modifiedParams[p.name] : p.value;
            return '<tr>'
                + '<td><span class="param-name">' + esc(p.name) + '</span></td>'
                + '<td><span class="param-desc">' + esc(p.description || '—') + '</span></td>'
                + '<td><input type="text" class="param-input' + (isMod ? ' modified' : '') + '" '
                + 'data-param="' + esc(p.name) + '" value="' + esc(String(curVal)) + '" '
                + 'onchange="window.onParamInputChange(\'' + esc(p.name) + '\', this.value)"></td>'
                + '</tr>';
        }).join('');
    }

    window.onParamInputChange = function (paramName, newVal) {
        modifiedParams[paramName] = newVal;
        var input = document.querySelector('input[data-param="' + paramName + '"]');
        if (input) { input.classList.add('modified'); }
        updateSaveButtonState();
    };

    btnSaveAllParams.addEventListener('click', function () {
        if (Object.keys(modifiedParams).length === 0) {
            showToast('No parameters have been modified.');
            return;
        }
        var count = Object.keys(modifiedParams).length;
        showToast('Writing ' + count + ' parameter' + (count > 1 ? 's' : '') + ' to Pixhawk...');
        btnSaveAllParams.disabled = true;
        btnSaveAllParams.textContent = '⏳ Saving ' + count + ' param' + (count > 1 ? 's' : '') + '...';
        apiRequest('/api/params/save', 'POST', { params: modifiedParams })
            .then(function (res) {
                btnSaveAllParams.disabled = false;
                showToast(res.message);
                modifiedParams = {};
                updateSaveButtonState();
                loadParams(paramSearchInput.value.trim());
            }).catch(function (err) {
                btnSaveAllParams.disabled = false;
                updateSaveButtonState();
                showToast('Error saving: ' + err.message, true);
            });
    });

    function setParamSourceBadge(source, file) {
        var badge = el('paramSourceBadge');
        if (!badge) { return; }
        if (source === 'live') {
            badge.textContent = '🛰️ Source: Live MAVLink';
            badge.className = 'param-source-badge live';
        } else if (source === 'local') {
            badge.textContent = '📁 Source: Local Cache File (' + (file || 'solo.param') + ')';
            badge.className = 'param-source-badge local';
        }
    }

    var searchDebounce = null;
    var fullParams = [];
    var knownDesc = {};

    function applyParamFilter() {
        var q = paramSearchInput.value.trim().toLowerCase();
        var rows = !q ? fullParams : fullParams.filter(function (p) {
            return String(p.name).toLowerCase().indexOf(q) !== -1
                || String(p.description || '').toLowerCase().indexOf(q) !== -1
                || String(p.value).toLowerCase().indexOf(q) !== -1;
        });
        allParams = rows;
        paramSearchCount.textContent = 'Showing ' + rows.length + ' of ' + fullParams.length + ' parameters';
        renderParamTable(rows);
    }

    var fetchProgressTimer = null;

    function renderFetchButtonProgress(status) {
        if (!btnFetchLive) return;
        var current = status.current || 0;
        var total = status.total || 0;
        var pct = status.percent || 0;
        var phase = status.phase || 'running';

        if (total > 0 && current > 0) {
            pct = Math.max(pct, Math.min(100, Math.round((current / total) * 100)));
        }

        var label = '⏳ Fetching...';
        if (phase === 'connecting') {
            label = '📡 Connecting...';
        } else if (phase === 'mavftp') {
            if (total > 0 && current > 0) {
                label = '⚡ MAVFTP ' + current + ' / ' + total + ' (' + pct + '%)';
            } else if (current > 0) {
                label = '⚡ MAVFTP ' + current + ' B';
            } else {
                label = '⚡ MAVFTP reading...';
            }
        } else if (phase === 'retry') {
            label = '🔄 Retry ' + current + ' / ' + total + ' (' + pct + '%)';
        } else if (total > 0 && current > 0) {
            label = '📡 ' + current + ' / ' + total + ' (' + pct + '%)';
        } else if (current > 0) {
            label = '📡 ' + current + ' params';
        } else if (pct > 0) {
            label = '⏳ ' + pct + '%';
        }

        btnFetchLive.textContent = label;
        btnFetchLive.style.background = 'linear-gradient(to right, #0284c7 0%, #0284c7 ' + pct + '%, var(--bg-card-alt, #1e293b) ' + pct + '%, var(--bg-card-alt, #1e293b) 100%)';
        btnFetchLive.style.borderColor = '#0284c7';
        btnFetchLive.style.color = '#ffffff';
        btnFetchLive.style.boxShadow = '0 0 10px rgba(2, 132, 199, 0.4)';
    }

    function resetFetchButton(success, message) {
        if (!btnFetchLive) return;
        if (fetchProgressTimer) {
            clearInterval(fetchProgressTimer);
            fetchProgressTimer = null;
        }
        if (success === true) {
            btnFetchLive.textContent = '✅ Fetched 100%';
            btnFetchLive.style.background = 'linear-gradient(to right, #16a34a 0%, #16a34a 100%)';
            btnFetchLive.style.borderColor = '#16a34a';
            btnFetchLive.style.color = '#ffffff';
            btnFetchLive.style.boxShadow = '0 0 12px rgba(22, 163, 74, 0.4)';
            setTimeout(function () {
                btnFetchLive.disabled = false;
                btnFetchLive.textContent = '📡 Fetch Live';
                btnFetchLive.style.background = '';
                btnFetchLive.style.borderColor = '';
                btnFetchLive.style.color = '';
                btnFetchLive.style.boxShadow = '';
            }, 1800);
        } else if (success === false) {
            btnFetchLive.textContent = '⚠️ Fetch Failed';
            btnFetchLive.style.background = 'rgba(239, 68, 68, 0.2)';
            btnFetchLive.style.borderColor = '#ef4444';
            btnFetchLive.style.color = '#ef4444';
            btnFetchLive.style.boxShadow = '';
            setTimeout(function () {
                btnFetchLive.disabled = false;
                btnFetchLive.textContent = '📡 Fetch Live';
                btnFetchLive.style.background = '';
                btnFetchLive.style.borderColor = '';
                btnFetchLive.style.color = '';
            }, 2500);
        } else {
            btnFetchLive.disabled = false;
            btnFetchLive.textContent = '📡 Fetch Live';
            btnFetchLive.style.background = '';
            btnFetchLive.style.borderColor = '';
            btnFetchLive.style.color = '';
            btnFetchLive.style.boxShadow = '';
        }
    }

    function startFetchProgressPolling() {
        if (fetchProgressTimer) {
            clearInterval(fetchProgressTimer);
        }
        fetchProgressTimer = setInterval(function () {
            apiRequest('/api/params/fetch-status', 'GET')
                .then(function (st) {
                    if (st) {
                        renderFetchButtonProgress(st);
                        if (!st.active && st.phase === 'done') {
                            clearInterval(fetchProgressTimer);
                            fetchProgressTimer = null;
                        }
                    }
                })
                .catch(function () {
                    // Ignore transient network errors during polling
                });
        }, 180);
    }

    btnFetchLive.addEventListener('click', function () {
        btnFetchLive.disabled = true;
        renderFetchButtonProgress({ current: 0, total: 0, percent: 0, phase: 'connecting' });
        startFetchProgressPolling();

        apiRequest('/api/params/fetch-live', 'POST')
            .then(function (res) {
                showToast(res.message || 'Live parameters fetched!');
                setParamSourceBadge(res.source || 'live', res.file);
                paramSearchInput.value = '';
                loadParams();
                resetFetchButton(true, res.message);
            }).catch(function (err) {
                showToast('MAVLink fetch: ' + err.message, true);
                var b = el('paramSourceBadge');
                if (b) { b.textContent = '⚠️ Live MAVLink unavailable'; b.className = 'param-source-badge'; }
                resetFetchButton(false, err.message);
            });
    });

    // Auto-detect ongoing fetch if page was reloaded during download
    try {
        apiRequest('/api/params/fetch-status', 'GET')
            .then(function (st) {
                if (st && st.active) {
                    btnFetchLive.disabled = true;
                    renderFetchButtonProgress(st);
                    startFetchProgressPolling();
                }
            })
            .catch(function () {});
    } catch (e) {}

    paramSearchInput.addEventListener('input', function () {
        if (fullParams.length) {
            applyParamFilter();   // instant client-side row hide/reveal
            return;
        }
        clearTimeout(searchDebounce);
        searchDebounce = setTimeout(function () {
            loadParams(paramSearchInput.value.trim());
        }, 220);
    });


    btnExportParam.addEventListener('click', function () {
        window.location.href = '/api/params/export';
    });

    fileImportParam.addEventListener('change', function (e) {
        var file = e.target.files[0];
        if (!file) { return; }
        var reader = new FileReader();
        reader.onload = function (ev) {
            apiRequest('/api/params/import', 'POST', { content: ev.target.result })
                .then(function (res) {
                    showToast(res.message);
                    loadParams();
                }).catch(function (err) { showToast('Import error: ' + err.message, true); });
        };
        reader.readAsText(file);
    });

    // =========================================================================
    // System Status Polling
    // =========================================================================
    function updateArmedSafetyState(armed) {
        isVehicleArmed = Boolean(armed);
        if (firmwareArmedLock) {
            firmwareArmedLock.style.display = isVehicleArmed ? 'block' : 'none';
        }
        if (wifiArmedLock) {
            wifiArmedLock.style.display = isVehicleArmed ? 'block' : 'none';
        }

        if (btnStartFlash) {
            if (isVehicleArmed) {
                btnStartFlash.disabled = true;
                btnStartFlash.title = 'ARMED SAFETY LOCKOUT: Cannot install firmware while vehicle is armed.';
            } else if (!flashPollInterval || btnStartFlash.textContent.indexOf('IN PROGRESS') === -1) {
                btnStartFlash.disabled = false;
                btnStartFlash.title = '';
            }
        }

        if (btnConnectWifi) {
            if (isVehicleArmed) {
                btnConnectWifi.disabled = true;
                btnConnectWifi.title = 'ARMED SAFETY INTERLOCK: Cannot connect while vehicle is armed.';
            } else if (btnConnectWifi.textContent.indexOf('Connecting') === -1) {
                btnConnectWifi.disabled = false;
                btnConnectWifi.title = '';
            }
        }

        var radioInputs = document.querySelectorAll('#tab-flash input[type="radio"]');
        for (var i = 0; i < radioInputs.length; i++) {
            radioInputs[i].disabled = isVehicleArmed;
        }
    }

    function pollArmedSafety() {
        fetch('/api/telemetry')
            .then(function (r) { return r.json(); })
            .then(function (telem) {
                var armed = false;
                if (telem && typeof telem.armed === 'boolean') {
                    armed = telem.armed;
                } else if (telem && telem.status && typeof telem.status.armed === 'boolean') {
                    armed = telem.status.armed;
                }
                updateArmedSafetyState(armed);
            })
            .catch(function () {});
    }

    function checkSystemStatus() {
        fetch('/api/status').then(function (r) { return r.json(); })
            .then(function (status) {
                var copterIsOnline = (status && status.copter_online === true);
                var controllerIsOnline = !!(status && (status.controller_online === true || status.paired === true));

                // STATE MACHINE GATE: ignore every copter_online reading until the installation
                // countdown is actively running (installationInProgress === true).
                if (installationInProgress) {
                    if (!copterIsOnline) {
                        // Track continuous offline time (drone must physically cut power first)
                        if (offlineSinceMs === null) { offlineSinceMs = Date.now(); }
                    } else if (offlineSinceMs !== null && (Date.now() - offlineSinceMs) >= REQUIRED_OFFLINE_MS
                               && (timerStarted || rebootCountdownInterval !== null)) {
                        // Verified: copter was offline >= 15s, now genuinely back online
                        if (rebootCountdownInterval) {
                            clearInterval(rebootCountdownInterval);
                            rebootCountdownInterval = null;
                        }
                        timerStarted = false;
                        isUpdateRunning = false;
                        installationInProgress = false;
                        offlineSinceMs = null;
                        try { localStorage.removeItem('solo_reboot_until'); } catch (e) {}
                        onCopterHardwareAlive();
                    } else {
                        // Premature 'true' (e.g., blip before power cut): ignore and restart offline clock
                        offlineSinceMs = null;
                    }
                }
                previousCopterOnline = copterIsOnline;

                if (copterIsOnline) {
                    linkStatusBadge.className = 'conn-badge online';
                    linkStatusText.textContent = 'Online (10.1.1.10)';
                } else {
                    linkStatusBadge.className = 'conn-badge offline';
                    linkStatusText.textContent = 'Link Lost';
                }
                updatePairGuardUI(copterIsOnline, controllerIsOnline);
                if (status && status.utility_version) {
                    headerVersionTag.textContent = 'v' + status.utility_version;
                    footerVersionText.textContent = 'Version ' + status.utility_version + ' (Iteration ' + (status.iteration || 1) + ')';
                }
                if (status && headerCopterVer) {
                    // Always mirror what the server knows about the Cube; never keep an old value
                    var cleanVer = (status.pixhawk_version || '').replace(/^ArduCopter\s*/i, '').replace(/unknown/gi, '').trim();
                    headerCopterVer.textContent = cleanVer || 'checking\u2026';
                    headerCopterVer.title = cleanVer ? 'Installed on the Cube' : 'Waiting for the Cube to report its version';
                }
                if (status && typeof status.armed === 'boolean') {
                    updateArmedSafetyState(status.armed);
                }
            }).catch(function () {
                previousCopterOnline = false;
                linkStatusBadge.className = 'conn-badge offline';
                linkStatusText.textContent = 'Link Lost';
                updatePairGuardUI(false, false);
            });
    }

    function updatePairGuardUI(isOnline, isControllerOnline) {
        var btnPair = document.getElementById('btnPairController');
        var pairNotice = document.getElementById('pairWifiNotice');
        var pairBadge = document.getElementById('pairStatusBadge');

        if (pairBadge) {
            if (!isOnline) {
                pairBadge.className = 'badge offline';
                pairBadge.textContent = 'Offline';
            } else if (isControllerOnline) {
                pairBadge.className = 'badge badge-success success';
                pairBadge.textContent = 'Paired & Connected';
            } else {
                pairBadge.className = 'badge badge-warning warning';
                pairBadge.textContent = 'Not Connected';
            }
        }

        if (btnPair) {
            if (isOnline) {
                btnPair.disabled = false;
                btnPair.classList.remove('disabled');
                btnPair.style.opacity = '1';
                btnPair.style.cursor = 'pointer';
                if (pairNotice) { pairNotice.style.display = 'none'; }
            } else {
                btnPair.disabled = true;
                btnPair.classList.add('disabled');
                btnPair.style.opacity = '0.5';
                btnPair.style.cursor = 'not-allowed';
                if (pairNotice) { pairNotice.style.display = 'inline'; }
            }
        }
    }

    var btnPairController = document.getElementById('btnPairController');
    if (btnPairController) {
        btnPairController.addEventListener('click', function (e) {
            if (btnPairController.disabled) {
                e.preventDefault();
                showToast('Wi-Fi Connection Required: The SoloLink Wi-Fi network must also be connected to continue.', 'error');
                return;
            }
            btnPairController.disabled = true;
            btnPairController.textContent = '⏳ Pairing...';
            apiRequest('/api/pair/start', 'POST', {})
            .then(function (data) {
                btnPairController.disabled = false;
                btnPairController.textContent = '🔗 Pair Copter & Controller';
                if (data && data.error) {
                    showToast(data.message || 'Wi-Fi Connection Required: The SoloLink Wi-Fi network must also be connected to continue.', 'error');
                } else {
                    showToast((data && data.message) || 'Pairing initiated.', 'success');
                    checkSystemStatus();
                }
            })
            .catch(function (err) {
                btnPairController.disabled = false;
                btnPairController.textContent = '🔗 Pair Copter & Controller';
                showToast((err && err.message) || 'Wi-Fi Connection Required: The SoloLink Wi-Fi network must also be connected to continue.', 'error');
            });
        });
    }

    // =========================================================================
    // XSS-safe HTML escaper
    // =========================================================================
    function esc(str) {
        if (typeof str !== 'string') { str = String(str); }
        return str.replace(/[&<>"']/g, function (c) {
            return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c] || c;
        });
    }

    // =========================================================================
    // Controller Setup (extSettings.conf)
    // =========================================================================
    var ctrlDefinitions = null;
    var ctrlCurrentConfig = { mappings: {}, timeOffsetHrs: "None", raw_text: "" };
    var ctrlOriginalRaw = "";
    var ctrlSelectedBtnId = 4;

    var CTRL_PRESETS = {
        default: {
            name: "Default OpenSolo",
            mappings: {
                "4,7": { shot: -1, mode: 0, func: -1 },
                "4,5": { shot: -1, mode: 3, func: -1 },
                "5,7": { shot: -1, mode: 11, func: -1 },
                "5,5": { shot: -1, mode: 13, func: -1 },
                "6,5": { shot: -1, mode: -1, func: -1 },
                "7,5": { shot: -1, mode: -1, func: -1 },
                "8,7": { shot: -1, mode: -1, func: -1 },
                "8,5": { shot: -1, mode: -1, func: 3 }
            },
            timeOffsetHrs: "None"
        },
        cinematography: {
            name: "Cinematography & Shots",
            mappings: {
                "4,7": { shot: 2, mode: -1, func: -1 },
                "4,5": { shot: 1, mode: -1, func: -1 },
                "5,7": { shot: 0, mode: -1, func: -1 },
                "5,5": { shot: 7, mode: -1, func: -1 },
                "6,5": { shot: 3, mode: -1, func: -1 },
                "7,5": { shot: 5, mode: -1, func: -1 },
                "8,7": { shot: -1, mode: -1, func: -1 },
                "8,5": { shot: -1, mode: -1, func: 3 }
            },
            timeOffsetHrs: "None"
        },
        acro_sport: {
            name: "Acro & Sport Pilot",
            mappings: {
                "4,7": { shot: -1, mode: 1, func: -1 },
                "4,5": { shot: -1, mode: 14, func: -1 },
                "5,7": { shot: -1, mode: 13, func: -1 },
                "5,5": { shot: -1, mode: 15, func: -1 },
                "6,5": { shot: -1, mode: 0, func: -1 },
                "7,5": { shot: -1, mode: 17, func: -1 },
                "8,7": { shot: -1, mode: -1, func: -1 },
                "8,5": { shot: -1, mode: -1, func: 3 }
            },
            timeOffsetHrs: "None"
        },
        payload_gripper: {
            name: "Payload / Gripper / Servo",
            mappings: {
                "4,7": { shot: -1, mode: -1, func: 4 },
                "4,5": { shot: -1, mode: -1, func: 6 },
                "5,7": { shot: -1, mode: -1, func: 13 },
                "5,5": { shot: -1, mode: -1, func: 14 },
                "6,5": { shot: -1, mode: -1, func: 7 },
                "7,5": { shot: -1, mode: -1, func: 9 },
                "8,7": { shot: -1, mode: -1, func: -1 },
                "8,5": { shot: -1, mode: -1, func: 3 }
            },
            timeOffsetHrs: "None"
        },
        survey_smartrtl: {
            name: "Survey & SmartRTL",
            mappings: {
                "4,7": { shot: -1, mode: 3, func: -1 },
                "4,5": { shot: -1, mode: 17, func: -1 },
                "5,7": { shot: -1, mode: 21, func: -1 },
                "5,5": { shot: -1, mode: 9, func: -1 },
                "6,5": { shot: 9, mode: -1, func: -1 },
                "7,5": { shot: 7, mode: -1, func: -1 },
                "8,7": { shot: -1, mode: -1, func: -1 },
                "8,5": { shot: -1, mode: -1, func: 3 }
            },
            timeOffsetHrs: "None"
        }
    };

    function setCtrlDirty(isDirty) {
        var btnSave = el('btnCtrlSave');
        if (!btnSave) { return; }
        if (isDirty) {
            btnSave.classList.add('has-unsaved');
            btnSave.innerHTML = '💾 Save Controller Settings ⚠️';
            btnSave.title = 'You have unsaved changes! Click to review and save to Solo copter.';
        } else {
            btnSave.classList.remove('has-unsaved');
            btnSave.innerHTML = '💾 Save Controller Settings';
            btnSave.title = 'Review and write changes to Solo copter';
        }
    }

    function initControllerSetup() {
        var btnFetch = el('btnCtrlFetch');
        var btnPresets = el('btnCtrlPresets');
        var btnRaw = el('btnCtrlRaw');
        var btnSave = el('btnCtrlSave');

        if (!btnFetch) { return; }

        btnFetch.addEventListener('click', function () { loadControllerConfig(true); });
        if (btnPresets) { btnPresets.addEventListener('click', openCtrlPresetsModal); }
        if (btnRaw) { btnRaw.addEventListener('click', openCtrlRawModal); }
        if (btnSave) { btnSave.addEventListener('click', openCtrlConfirmModal); }

        var inputTime = el('inputCtrlTimeOffset');
        var displayTime = el('ctrlTimeOffsetDisplay');
        if (inputTime) {
            inputTime.addEventListener('input', function (e) {
                var v = e.target.value.trim();
                ctrlCurrentConfig.timeOffsetHrs = v ? v : 'None';
                if (displayTime) { displayTime.textContent = ctrlCurrentConfig.timeOffsetHrs; }
                setCtrlDirty(true);
            });
        }
        var btnTimeNone = el('btnCtrlTimeNone');
        if (btnTimeNone) {
            btnTimeNone.addEventListener('click', function () {
                ctrlCurrentConfig.timeOffsetHrs = 'None';
                if (inputTime) { inputTime.value = ''; }
                if (displayTime) { displayTime.textContent = 'None'; }
                setCtrlDirty(true);
            });
        }
        var btnTimeLocal = el('btnCtrlTimeLocal');
        if (btnTimeLocal) {
            btnTimeLocal.addEventListener('click', function () {
                var offsetHours = -new Date().getTimezoneOffset() / 60;
                var str = offsetHours >= 0 ? '+' + offsetHours : String(offsetHours);
                ctrlCurrentConfig.timeOffsetHrs = str;
                if (inputTime) { inputTime.value = offsetHours; }
                if (displayTime) { displayTime.textContent = str; }
                setCtrlDirty(true);
            });
        }

        var btnClosePresets = el('btnCloseCtrlPresets');
        if (btnClosePresets) {
            btnClosePresets.addEventListener('click', function () {
                var m = el('ctrlPresetsModal');
                if (m) { m.style.display = 'none'; }
            });
        }
        var btnCloseRaw = el('btnCloseCtrlRaw');
        if (btnCloseRaw) {
            btnCloseRaw.addEventListener('click', function () {
                var m = el('ctrlRawModal');
                if (m) { m.style.display = 'none'; }
            });
        }
        var btnCloseConfirm = el('btnCloseCtrlConfirm');
        if (btnCloseConfirm) {
            btnCloseConfirm.addEventListener('click', function () {
                var m = el('ctrlConfirmModal');
                if (m) { m.style.display = 'none'; }
            });
        }
        var btnCancelConfirm = el('btnCtrlCancelConfirm');
        if (btnCancelConfirm) {
            btnCancelConfirm.addEventListener('click', function () {
                var m = el('ctrlConfirmModal');
                if (m) { m.style.display = 'none'; }
            });
        }

        document.querySelectorAll('#ctrlPresetsModal .preset-card').forEach(function (card) {
            card.addEventListener('click', function () {
                var key = card.getAttribute('data-preset');
                applyCtrlPreset(key);
            });
        });

        var btnRawRevert = el('btnCtrlRawRevert');
        if (btnRawRevert) {
            btnRawRevert.addEventListener('click', function () {
                var ta = el('ctrlRawConfigTextarea');
                if (ta) { ta.value = buildCtrlRawFromCurrent(); }
            });
        }
        var btnRawApply = el('btnCtrlRawApply');
        if (btnRawApply) {
            btnRawApply.addEventListener('click', function () {
                var ta = el('ctrlRawConfigTextarea');
                if (ta) {
                    parseCtrlRawIntoCurrent(ta.value);
                    var m = el('ctrlRawModal');
                    if (m) { m.style.display = 'none'; }
                    renderCtrlButtonCards();
                    showToast('Parsed raw text into controller settings!');
                }
            });
        }

        var btnExecSave = el('btnCtrlExecuteSave');
        if (btnExecSave) {
            btnExecSave.addEventListener('click', executeCtrlSave);
        }

        document.querySelectorAll('#tab-controller .interactive-btn').forEach(function (elBtn) {
            elBtn.addEventListener('click', function () {
                var id = parseInt(elBtn.getAttribute('data-btn-id'), 10);
                selectCtrlButton(id);
            });
        });

        [4, 5, 6, 7, 8].forEach(function (id) {
            var leg = el('ctrlLegendBtn' + id);
            if (leg) {
                leg.addEventListener('click', function () { selectCtrlButton(id); });
            }
        });

        apiRequest('/api/controller/definitions')
            .then(function (defs) {
                ctrlDefinitions = defs;
                return loadControllerConfig(false);
            })
            .catch(function (err) {
                console.error('Failed to load controller definitions', err);
            });
    }

    function loadControllerConfig(isManual) {
        return apiRequest('/api/controller/config')
            .then(function (data) {
                ctrlCurrentConfig = data;
                ctrlOriginalRaw = data.raw_text || '';

                var badge = el('ctrlStatusBadge');
                if (badge) {
                    if (data.is_live) {
                        badge.textContent = '🟢 Live on Solo 10.1.1.10';
                        badge.className = 'badge badge-success';
                    } else {
                        badge.textContent = '🟡 Local Template';
                        badge.className = 'badge badge-warning';
                    }
                }

                var displayTime = el('ctrlTimeOffsetDisplay');
                var inputTime = el('inputCtrlTimeOffset');
                if (displayTime) { displayTime.textContent = ctrlCurrentConfig.timeOffsetHrs || 'None'; }
                if (inputTime) {
                    inputTime.value = (ctrlCurrentConfig.timeOffsetHrs && ctrlCurrentConfig.timeOffsetHrs !== 'None') ? parseFloat(ctrlCurrentConfig.timeOffsetHrs) : '';
                }

                renderCtrlButtonCards();
                updateCtrlLegend();
                setCtrlDirty(false);
                if (isManual) { showToast('Controller configuration reloaded from Solo!'); }
            })
            .catch(function (err) {
                showToast('Failed to load controller config: ' + err.message, 'error');
            });
    }

    function renderCtrlButtonCards() {
        if (!ctrlDefinitions) { return; }
        var container = el('ctrlButtonCardsContainer');
        if (!container) { return; }
        container.innerHTML = '';

        ctrlDefinitions.buttons.forEach(function (btn) {
            var card = document.createElement('div');
            card.className = 'card btn-config-card' + (btn.id === ctrlSelectedBtnId ? ' selected' : '');
            card.id = 'ctrlCardBtn' + btn.id;

            var header = document.createElement('div');
            header.className = 'btn-card-header';
            header.innerHTML = '<div class="btn-title-wrap">' +
                '<div class="btn-ident-bubble ' + (btn.id >= 6 ? 'sub' : '') + '">' +
                (btn.id === 8 ? '📷' : (btn.id === 4 ? 'A' : (btn.id === 5 ? 'B' : (btn.id === 6 ? '1' : '2')))) +
                '</div>' +
                '<div>' +
                    '<div class="btn-ident-text">' + esc(btn.name) + '</div>' +
                    '<div class="btn-ident-desc">' + esc(btn.description) + '</div>' +
                '</div>' +
            '</div>' +
            '<span class="badge badge-accent">ID ' + btn.id + '</span>';
            card.appendChild(header);

            var body = document.createElement('div');
            body.className = 'btn-card-body';

            btn.supported_events.forEach(function (eventId) {
                var eventDef = ctrlDefinitions.events.find(function (e) { return e.id === eventId; });
                var lookupKey = btn.id + ',' + eventId;
                var mapping = ctrlCurrentConfig.mappings[lookupKey] || { shot: -1, mode: -1, func: -1 };

                var eventRow = document.createElement('div');
                eventRow.className = 'event-row';
                eventRow.id = 'ctrlRow-' + btn.id + '-' + eventId;

                var eventHeader = document.createElement('div');
                eventHeader.className = 'event-header';
                eventHeader.innerHTML = '<div class="event-label-wrap">' +
                    '<span class="event-pill ' + (eventId === 7 ? 'short' : 'long') + '">' +
                        esc(eventDef ? eventDef.short_label : 'Hold') +
                    '</span>' +
                    '<span class="event-name">' + esc(eventDef ? eventDef.name : 'Event ' + eventId) + '</span>' +
                '</div>' +
                '<span class="event-key-badge">Key: [' + lookupKey + ']</span>';
                eventRow.appendChild(eventHeader);

                var selectWrap = document.createElement('div');
                selectWrap.className = 'unified-select-wrap';

                var actionSelect = document.createElement('select');
                actionSelect.className = 'action-dropdown';

                var optNone = document.createElement('option');
                optNone.value = 'none';
                optNone.textContent = '⛔ None / Unassigned';
                actionSelect.appendChild(optNone);

                var grpModes = document.createElement('optgroup');
                grpModes.label = '🚀 Flight Modes';
                ctrlDefinitions.modes.forEach(function (m) {
                    if (m.id === -1) { return; }
                    var opt = document.createElement('option');
                    opt.value = 'mode:' + m.id;
                    opt.textContent = m.name;
                    if (m.disabled) { opt.disabled = true; }
                    grpModes.appendChild(opt);
                });
                actionSelect.appendChild(grpModes);

                var grpShots = document.createElement('optgroup');
                grpShots.label = '🎬 Smart Shots';
                ctrlDefinitions.shots.forEach(function (s) {
                    if (s.id === -1) { return; }
                    var opt = document.createElement('option');
                    opt.value = 'shot:' + s.id;
                    opt.textContent = s.name;
                    grpShots.appendChild(opt);
                });
                actionSelect.appendChild(grpShots);

                var grpFuncs = document.createElement('optgroup');
                grpFuncs.label = '⚙️ Auxiliary / Gimbal / Servos';
                ctrlDefinitions.functions.forEach(function (f) {
                    if (f.id === -1) { return; }
                    var opt = document.createElement('option');
                    opt.value = 'func:' + f.id;
                    opt.textContent = f.name;
                    if (f.disabled) { opt.disabled = true; }
                    grpFuncs.appendChild(opt);
                });
                actionSelect.appendChild(grpFuncs);

                if (mapping.shot >= 0) {
                    actionSelect.value = 'shot:' + mapping.shot;
                } else if (mapping.mode >= 0) {
                    actionSelect.value = 'mode:' + mapping.mode;
                } else if (mapping.func >= 0) {
                    actionSelect.value = 'func:' + mapping.func;
                } else {
                    actionSelect.value = 'none';
                }

                var summaryTag = document.createElement('div');
                summaryTag.className = 'action-summary-badge';
                summaryTag.textContent = formatCtrlSummary(mapping);

                selectWrap.appendChild(actionSelect);
                selectWrap.appendChild(summaryTag);
                eventRow.appendChild(selectWrap);

                actionSelect.addEventListener('change', function () {
                    var val = actionSelect.value;
                    if (val === 'none') {
                        mapping.shot = -1;
                        mapping.mode = -1;
                        mapping.func = -1;
                    } else if (val.indexOf('mode:') === 0) {
                        mapping.mode = parseInt(val.split(':')[1], 10);
                        mapping.shot = -1;
                        mapping.func = -1;
                    } else if (val.indexOf('shot:') === 0) {
                        mapping.shot = parseInt(val.split(':')[1], 10);
                        mapping.mode = -1;
                        mapping.func = -1;
                    } else if (val.indexOf('func:') === 0) {
                        mapping.func = parseInt(val.split(':')[1], 10);
                        mapping.shot = -1;
                        mapping.mode = -1;
                    }

                    ctrlCurrentConfig.mappings[lookupKey] = mapping;
                    summaryTag.textContent = formatCtrlSummary(mapping);
                    updateCtrlLegend();
                    setCtrlDirty(true);
                });

                body.appendChild(eventRow);
            });

            card.appendChild(body);
            container.appendChild(card);
        });
    }

    function formatCtrlSummary(mapping) {
        var parts = [];
        if (mapping.shot >= 0 && ctrlDefinitions) {
            var s = ctrlDefinitions.shots.find(function (x) { return x.id === mapping.shot; });
            parts.push('Shot: ' + (s ? s.name : mapping.shot));
        }
        if (mapping.mode >= 0 && ctrlDefinitions) {
            var m = ctrlDefinitions.modes.find(function (x) { return x.id === mapping.mode; });
            var shortMode = m ? m.name.split('(')[0].trim() : mapping.mode;
            parts.push('Mode: ' + shortMode);
        }
        if (mapping.func >= 0 && ctrlDefinitions) {
            var f = ctrlDefinitions.functions.find(function (x) { return x.id === mapping.func; });
            var shortFunc = f ? f.name.split('(')[0].trim() : mapping.func;
            parts.push('Aux: ' + shortFunc);
        }
        return parts.length > 0 ? parts.join(' | ') : 'Disabled (None)';
    }

    function updateCtrlLegend() {
        if (!ctrlDefinitions) { return; }
        [4, 5, 6, 7, 8].forEach(function (id) {
            var leg = el('ctrlLegendSummary' + id);
            if (!leg) { return; }

            var shortKey = id + ',7';
            var longKey = id + ',5';
            var shortMap = ctrlCurrentConfig.mappings[shortKey];
            var longMap = ctrlCurrentConfig.mappings[longKey];

            var summaries = [];
            if (shortMap && (shortMap.shot >= 0 || shortMap.mode >= 0 || shortMap.func >= 0)) {
                summaries.push('Short: ' + formatCtrlSummary(shortMap));
            }
            if (longMap && (longMap.shot >= 0 || longMap.mode >= 0 || longMap.func >= 0)) {
                summaries.push('Long: ' + formatCtrlSummary(longMap));
            }

            leg.textContent = summaries.length > 0 ? summaries.join(' • ') : 'Unassigned / Stock';
        });
    }

    function selectCtrlButton(btnId) {
        ctrlSelectedBtnId = btnId;

        document.querySelectorAll('#ctrlButtonCardsContainer .btn-config-card').forEach(function (c) {
            c.classList.remove('selected');
        });
        var targetCard = el('ctrlCardBtn' + btnId);
        if (targetCard) {
            targetCard.classList.add('selected');
            targetCard.scrollIntoView({ behavior: 'smooth', block: 'center' });
        }

        document.querySelectorAll('#tab-controller .interactive-btn').forEach(function (b) {
            b.classList.remove('active');
        });
        var targetSvg = el('ctrlSvgBtn' + btnId);
        if (targetSvg) { targetSvg.classList.add('active'); }

        [4, 5, 6, 7, 8].forEach(function (id) {
            var l = el('ctrlLegendBtn' + id);
            if (l) {
                if (id === btnId) { l.classList.add('active'); }
                else { l.classList.remove('active'); }
            }
        });
    }

    function openCtrlPresetsModal() {
        var m = el('ctrlPresetsModal');
        if (m) { m.style.display = 'flex'; }
    }

    function applyCtrlPreset(key) {
        var profile = CTRL_PRESETS[key];
        if (!profile) { return; }

        ctrlCurrentConfig.mappings = JSON.parse(JSON.stringify(profile.mappings));
        ctrlCurrentConfig.timeOffsetHrs = profile.timeOffsetHrs || 'None';

        var dt = el('ctrlTimeOffsetDisplay');
        var it = el('inputCtrlTimeOffset');
        if (dt) { dt.textContent = ctrlCurrentConfig.timeOffsetHrs; }
        if (it) { it.value = ctrlCurrentConfig.timeOffsetHrs === 'None' ? '' : ctrlCurrentConfig.timeOffsetHrs; }

        renderCtrlButtonCards();
        updateCtrlLegend();

        var m = el('ctrlPresetsModal');
        if (m) { m.style.display = 'none'; }
        showToast('Applied preset profile: "' + profile.name + '"!');
    }

    function buildCtrlRawFromCurrent() {
        var lines = ['[extFunctions]'];
        var keys = Object.keys(ctrlCurrentConfig.mappings).sort();

        keys.forEach(function (k) {
            var item = ctrlCurrentConfig.mappings[k];
            var s = item.shot !== undefined ? item.shot : -1;
            var m = item.mode !== undefined ? item.mode : -1;
            var f = item.func !== undefined ? item.func : -1;
            lines.push(k + '=' + s + ', ' + m + ', ' + f);
        });

        lines.push('timeOffsetHrs=' + (ctrlCurrentConfig.timeOffsetHrs || 'None'));
        lines.push('');
        lines.push('');

        if (ctrlOriginalRaw && ctrlOriginalRaw.indexOf('[USAGE]') !== -1) {
            lines.push(ctrlOriginalRaw.substring(ctrlOriginalRaw.indexOf('[USAGE]')).trim());
        } else {
            lines.push('[USAGE]\nUSAGE: {button},{event} = {shot}, {mode}, {func}');
        }

        return lines.join('\n') + '\n';
    }

    function parseCtrlRawIntoCurrent(raw) {
        var lines = raw.split('\n');
        var inExt = false;
        var newMappings = {};
        var timeOffset = 'None';

        lines.forEach(function (line) {
            var trimmed = line.trim();
            if (!trimmed || trimmed.indexOf('#') === 0 || trimmed.indexOf(';') === 0) { return; }
            if (trimmed.indexOf('[') === 0) {
                inExt = (trimmed.replace(/[\[\]]/g, '').trim().toLowerCase() === 'extfunctions');
                return;
            }

            if (inExt && trimmed.indexOf('=') !== -1) {
                var eqIdx = trimmed.indexOf('=');
                var k = trimmed.substring(0, eqIdx).trim();
                var v = trimmed.substring(eqIdx + 1).trim();

                if (k.toLowerCase() === 'timeoffsethrs') {
                    timeOffset = v;
                } else {
                    var parts = v.split(',').map(function (s) { return parseInt(s.trim(), 10) || -1; });
                    newMappings[k] = {
                        shot: parts[0] !== undefined ? parts[0] : -1,
                        mode: parts[1] !== undefined ? parts[1] : -1,
                        func: parts[2] !== undefined ? parts[2] : -1
                    };
                }
            }
        });

        ctrlCurrentConfig.mappings = newMappings;
        ctrlCurrentConfig.timeOffsetHrs = timeOffset;
        ctrlCurrentConfig.raw_text = raw;

        var dt = el('ctrlTimeOffsetDisplay');
        var it = el('inputCtrlTimeOffset');
        if (dt) { dt.textContent = timeOffset; }
        if (it) { it.value = timeOffset === 'None' ? '' : timeOffset; }
    }

    function openCtrlRawModal() {
        var rawText = buildCtrlRawFromCurrent();
        var ta = el('ctrlRawConfigTextarea');
        if (ta) { ta.value = rawText; }
        var m = el('ctrlRawModal');
        if (m) { m.style.display = 'flex'; }
    }

    function openCtrlConfirmModal() {
        var newRaw = buildCtrlRawFromCurrent();
        var diffBox = el('ctrlDiffContainer');
        if (!diffBox) { return; }
        diffBox.innerHTML = '';

        var origLines = ctrlOriginalRaw.split('\n');
        var newLines = newRaw.split('\n');
        var maxLines = Math.max(origLines.length, newLines.length);
        var diffCount = 0;

        for (var i = 0; i < maxLines; i++) {
            var o = origLines[i] !== undefined ? origLines[i] : null;
            var n = newLines[i] !== undefined ? newLines[i] : null;

            if (o !== n) {
                diffCount++;
                if (o !== null) {
                    var spanRem = document.createElement('span');
                    spanRem.className = 'diff-line diff-rem';
                    spanRem.textContent = '- ' + o;
                    diffBox.appendChild(spanRem);
                }
                if (n !== null) {
                    var spanAdd = document.createElement('span');
                    spanAdd.className = 'diff-line diff-add';
                    spanAdd.textContent = '+ ' + n;
                    diffBox.appendChild(spanAdd);
                }
            } else if (i < 10 && n) {
                var spanSame = document.createElement('span');
                spanSame.className = 'diff-line diff-same';
                spanSame.textContent = '  ' + n;
                diffBox.appendChild(spanSame);
            }
        }

        if (diffCount === 0) {
            var spanNone = document.createElement('span');
            spanNone.className = 'diff-line diff-same';
            spanNone.textContent = '  (No differences from current configuration)';
            diffBox.appendChild(spanNone);
        }

        var m = el('ctrlConfirmModal');
        if (m) { m.style.display = 'flex'; }
    }

    function executeCtrlSave() {
        var btn = el('btnCtrlExecuteSave');
        if (btn) {
            btn.disabled = true;
            btn.textContent = 'Writing to Solo...';
        }

        var newRaw = buildCtrlRawFromCurrent();

        apiRequest('/api/controller/config', 'POST', {
            raw_text: newRaw,
            mappings: ctrlCurrentConfig.mappings,
            timeOffsetHrs: ctrlCurrentConfig.timeOffsetHrs
        })
        .then(function (res) {
            if (btn) {
                btn.disabled = false;
                btn.textContent = '✅ Confirm & Write to Solo';
            }
            var m = el('ctrlConfirmModal');
            if (m) { m.style.display = 'none'; }

            if (res.remote_saved || res.success) {
                ctrlOriginalRaw = newRaw;
                setCtrlDirty(false);
                showToast(res.message || '🎉 Successfully saved to Solo copter! Changes take effect immediately.');
            } else {
                showToast('Failed to save: ' + (res.error || 'Unknown error'), 'error');
            }
        })
        .catch(function (err) {
            if (btn) {
                btn.disabled = false;
                btn.textContent = '✅ Confirm & Write to Solo';
            }
            showToast('Save error: ' + err.message, 'error');
        });
    }

    // =========================================================================
    // Initialization
    // =========================================================================
    function initAppData() {
        loadVersionInfo();
        checkSystemStatus();
        setInterval(checkSystemStatus, 1000);
        pollArmedSafety();
        setInterval(pollArmedSafety, 1500);
        loadPresets();
        loadParams();
        refreshWifiStatus();
        initControllerSetup();
        loadFirmwareChoices();

        // Check calibration status on load
        apiRequest('/api/calib/status')
            .then(function (state) {
                updateCalibUI(state);
                if (state.is_running) {
                    startCalibPolling();
                }
            }).catch(function () {});

        // Uninstall Web Utility button handler
        var btnUninstall = el('btnUninstallApp');
        if (btnUninstall) {
            btnUninstall.addEventListener('click', function () {
                var confirmed = window.confirm(
                    "Are you sure you want to completely uninstall SoloWebUtility2 from the drone?\n\n" +
                    "This will:\n" +
                    "• Remove the auto-start entry from /etc/rc.local\n" +
                    "• Delete /usr/bin/SoloWebUtility2 and /log/SoloWebUtility2\n" +
                    "• Stop the web server process\n\n" +
                    "Click OK to proceed with uninstallation."
                );
                if (!confirmed) return;

                btnUninstall.disabled = true;
                btnUninstall.textContent = "Uninstalling...";

                apiRequest('/api/uninstall', 'POST', {})
                    .then(function (res) {
                        showToast("SoloWebUtility2 uninstalled successfully. Server is shutting down.");
                        document.body.innerHTML = '<div style="display:flex;justify-content:center;align-items:center;height:100vh;background:#0b101b;color:#fca5a5;font-family:sans-serif;text-align:center;"><div><h1>🗑️ SoloWebUtility2 Uninstalled</h1><p style="color:#cbd5e1;margin-top:10px;">The application files and /etc/rc.local startup entry have been completely removed from the drone.</p><p style="color:#94a3b8;margin-top:5px;">The server process has shut down. You may now close this tab.</p></div></div>';
                    })
                    .catch(function (err) {
                        showToast("Uninstall error: " + err.message, 'error');
                        btnUninstall.disabled = false;
                        btnUninstall.textContent = "🗑️ Uninstall Web Utility";
                    });
            });
        }

        // Resume reboot countdown timer if active
        try {
            var savedRebootUntil = localStorage.getItem('solo_reboot_until');
            if (savedRebootUntil) {
                var rem = Math.round((parseInt(savedRebootUntil, 10) - Date.now()) / 1000);
                if (rem > 0) {
                    flashProgressBar.style.width = '100%';
                    flashPercentLabel.textContent = '100%';
                    flashStageLabel.textContent = 'Hardware Rebooting — Applying Updates (~6 min)';
                    pipelineStatusBadge.textContent = 'REBOOTING';
                    pipelineStatusBadge.className = 'badge badge-accent';
                    stepCube.className       = 'step-item done';
                    stepCopter.className     = 'step-item done';
                    stepController.className = 'step-item done';
                    stepReboot.className     = 'step-item active';
                    startRebootCountdown(rem);
                } else {
                    localStorage.removeItem('solo_reboot_until');
                }
            }
        } catch (e) {}
    }

    // On page load: check auth
    if (authToken) {
        showLoginModal(false);
        initAppData();
    } else {
        showLoginModal(true);
    }

})();
