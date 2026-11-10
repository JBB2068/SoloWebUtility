#!/usr/bin/env node
/* Tests for the flight log viewer's .BIN parser (static/log-viewer/dataflash-worker.js).
 * Builds a synthetic ArduCopter DataFlash log (about 3 minutes: arming, flight modes, GPS track,
 * battery drain, a warning, a battery failsafe error, parameters, a few bytes of garbage) and
 * checks the summary and the chart series.
 *   node tools/test_logviewer.js              run the tests
 *   node tools/test_logviewer.js --write F    also write the synthetic log to F
 */
'use strict';
const path = require('path');
const fs = require('fs');
const assert = require('assert');
const W = require(path.join(__dirname, '..', 'static', 'log-viewer', 'dataflash-worker.js'));

const SIZES = { b: 1, B: 1, M: 1, h: 2, H: 2, c: 2, C: 2, i: 4, I: 4, e: 4, E: 4, L: 4, f: 4, n: 4, N: 16, Z: 64, Q: 8, q: 8, d: 8 };
const TYPES = {
    PARM: [64, 'QNf', 'TimeUS,Name,Value'],
    MSG: [65, 'QZ', 'TimeUS,Message'],
    MODE: [66, 'QMBB', 'TimeUS,Mode,ModeNum,Rsn'],
    EV: [67, 'QB', 'TimeUS,Id'],
    ARM: [68, 'QBIBB', 'TimeUS,ArmState,ArmChecks,Forced,Method'],
    GPS: [69, 'QBBIHBcLLefff', 'TimeUS,I,Status,GMS,GWk,NSats,HDop,Lat,Lng,Alt,Spd,GCrs,VZ'],
    CTUN: [70, 'Qfffffff', 'TimeUS,ThI,ThO,DAlt,Alt,BAlt,DCRt,CRt'],
    ATT: [71, 'QccccCC', 'TimeUS,DesRoll,Roll,DesPitch,Pitch,DesYaw,Yaw'],
    BAT: [72, 'QBfffff', 'TimeUS,Inst,Volt,VoltR,Curr,CurrTot,EnrgTot'],
    VIBE: [73, 'QBfffI', 'TimeUS,IMU,VibeX,VibeY,VibeZ,Clip'],
    ERR: [74, 'QBB', 'TimeUS,Subsys,ECode']
};

function len(fmt) { return 3 + fmt.split('').reduce((a, c) => a + SIZES[c], 0); }

function makeLog() {
    const parts = [];
    function put(id, fmt, values) {
        const buf = Buffer.alloc(len(fmt));
        buf[0] = 0xA3; buf[1] = 0x95; buf[2] = id;
        let p = 3;
        fmt.split('').forEach((c, i) => {
            const v = values[i];
            switch (c) {
                case 'b': buf.writeInt8(v, p); break;
                case 'B': case 'M': buf.writeUInt8(v, p); break;
                case 'h': buf.writeInt16LE(v, p); break;
                case 'H': buf.writeUInt16LE(v, p); break;
                case 'c': buf.writeInt16LE(Math.round(v * 100), p); break;
                case 'C': buf.writeUInt16LE(Math.round(v * 100), p); break;
                case 'i': buf.writeInt32LE(v, p); break;
                case 'I': buf.writeUInt32LE(v, p); break;
                case 'e': buf.writeInt32LE(Math.round(v * 100), p); break;
                case 'E': buf.writeUInt32LE(Math.round(v * 100), p); break;
                case 'L': buf.writeInt32LE(Math.round(v * 1e7), p); break;
                case 'f': buf.writeFloatLE(v, p); break;
                case 'd': buf.writeDoubleLE(v, p); break;
                case 'Q': case 'q': buf.writeBigUInt64LE(BigInt(Math.round(v)), p); break;
                case 'n': case 'N': case 'Z': buf.write(String(v), p, SIZES[c], 'latin1'); break;
            }
            p += SIZES[c];
        });
        parts.push(buf);
    }
    // FMT for FMT, then every type
    put(128, 'BBnNZ', [128, 89, 'FMT', 'BBnNZ', 'Type,Length,Name,Format,Columns']);
    Object.keys(TYPES).forEach(name => {
        const [id, fmt, cols] = TYPES[name];
        put(128, 'BBnNZ', [id, len(fmt), name, fmt, cols]);
    });
    const T0 = 50e6;                       // the Cube's clock when logging started (us)
    const us = s => T0 + s * 1e6;
    const msg = (s, text) => put(65, TYPES.MSG[1], [us(s), text]);
    msg(0.1, 'ArduCopter V4.7.2 (abcdef12)');
    msg(0.2, 'Frame: QUAD/X');
    for (let k = 0; k < 300; k++) put(64, TYPES.PARM[1], [us(0.3), 'PARAM_' + k, k * 0.5]);
    put(64, TYPES.PARM[1], [us(0.3), 'PLND_ENABLED', 1]);
    const mode = (s, num) => put(66, TYPES.MODE[1], [us(s), num, num, 1]);
    mode(1, 0);
    // GPS week/ms for 2026-11-07 15:00:00 UTC (+18 s leap seconds)
    const gpsMs = Date.UTC(2026, 10, 7, 15, 0, 0) - Date.UTC(1980, 0, 6) + 18000;
    const gwk = Math.floor(gpsMs / 604800000), gms0 = gpsMs - gwk * 604800000;
    const lat0 = 32.8, lng0 = -97.1;
    for (let s = 2; s <= 180; s += 0.1) {
        s = Math.round(s * 10) / 10;
        const armed = s >= 10 && s < 170;
        const alt = !armed ? 0 : Math.min(30, (s - 10) * 1.5, (170 - s) * 1.5);
        const out = armed ? Math.min(1, Math.max(0, (s - 25) / 40), Math.max(0, (120 - s) / 40)) : 0;
        const north = out * 100;           // metres
        put(70, TYPES.CTUN[1], [us(s), 0.5, armed ? 0.45 : 0, alt + 0.5, alt, alt, 0, 0]);
        put(71, TYPES.ATT[1], [us(s), 1, 1 + Math.sin(s), -2, -2 + Math.cos(s), 90, 90]);
        if (Math.round(s * 10) % 2 === 0) {
            put(69, TYPES.GPS[1], [us(s), 0, 3, gms0 + Math.round(s * 1000), gwk, armed ? 14 : 12, 0.8,
                lat0 + north / 111320, lng0, 200 + alt, armed && s > 25 && s < 120 ? 2.5 : 0, 0, 0]);
        }
        if (Math.round(s * 10) % 10 === 0) {
            const used = armed ? (s - 10) * 12.5 : (s >= 170 ? 2000 : 0);
            put(72, TYPES.BAT[1], [us(s), 0, 16.6 - used / 2000 * 1.7, 16.7, armed ? 20 : 0.5, used, 0]);
            put(73, TYPES.VIBE[1], [us(s), 0, 3, 4, armed ? 12 : 1, 0]);
        }
        if (s === 10) { put(68, TYPES.ARM[1], [us(s), 1, 0, 0, 0]); put(67, TYPES.EV[1], [us(s), 10]); }
        if (s === 20) mode(s, 5);
        if (s === 120) mode(s, 6);
        if (s === 150) { mode(s, 9); msg(s, 'Battery 1 is low 14.9V used 1750 mAh'); }
        if (s === 160) put(74, TYPES.ERR[1], [us(s), 6, 1]);
        if (s === 170) { put(68, TYPES.ARM[1], [us(s), 0, 0, 0, 0]); put(67, TYPES.EV[1], [us(s), 11]); }
        if (s === 90) parts.push(Buffer.from([0x00, 0xA3, 0x11, 0x22, 0x95]));   // garbage to skip
    }
    return Buffer.concat(parts);
}

function run() {
    const b = makeLog();
    const out = process.argv.indexOf('--write');
    if (out > 0) fs.writeFileSync(process.argv[out + 1], b);
    const buf = b.buffer.slice(b.byteOffset, b.byteOffset + b.length);
    const log = W.index(buf);
    W._setLog(log);
    const s = W.summarize();

    assert.strictEqual(log.bad, 5, 'garbage bytes skipped');
    assert.strictEqual(s.version, 'ArduCopter V4.7.2 (abcdef12)');
    assert.ok(Math.abs(s.flightTime - 160) < 0.2, 'flight time ' + s.flightTime);
    assert.strictEqual(s.armed.length, 1);
    assert.ok(Math.abs(s.armed[0][0] - 9.9) < 0.2, 'armed at ' + s.armed[0][0]);
    assert.ok(Math.abs(s.duration - 179.9) < 0.3, 'duration ' + s.duration);
    assert.deepStrictEqual(s.modes.map(m => m.name), ['Stabilize', 'Loiter', 'RTL', 'Land']);
    assert.ok(Math.abs(s.maxAlt - 30) < 0.01, 'max alt ' + s.maxAlt);
    assert.ok(Math.abs(s.maxDist - 100) < 1, 'max dist ' + s.maxDist);
    assert.ok(Math.abs(s.path - 200) < 3, 'path ' + s.path);
    assert.ok(Math.abs(s.maxSpeed - 2.5) < 0.01);
    assert.deepStrictEqual(s.sats, [14, 14]);
    assert.ok(Math.abs(s.battery.start - 16.6) < 0.02, 'start V ' + s.battery.start);
    assert.ok(Math.abs(s.battery.min - 14.92) < 0.05, 'min V ' + s.battery.min);
    assert.ok(Math.abs(s.battery.used - 1987.5) < 1, 'used ' + s.battery.used);
    assert.ok(Math.abs(s.vibration.max - 12) < 0.01);
    assert.strictEqual(s.date, '2026-11-07T15:00:02.000Z');
    assert.strictEqual(s.params.length, 301);
    assert.strictEqual(s.params.filter(p => p.name === 'PLND_ENABLED')[0].value, 1);
    const errs = s.messages.filter(m => m.sev === 'error');
    assert.strictEqual(errs.length, 1);
    assert.strictEqual(errs[0].text, 'Battery failsafe: error code 1');
    assert.ok(s.messages.some(m => m.sev === 'warning' && /Battery 1 is low/.test(m.text)));
    for (let k = 1; k < s.messages.length; k++) assert.ok(s.messages[k].t >= s.messages[k - 1].t, 'messages in time order');
    assert.ok(s.types.find(t => t.name === 'ATT').fields.indexOf('DesRoll') >= 0);
    assert.ok(!s.types.find(t => t.name === 'MSG'), 'text-only types are not chartable');

    const ser = W.series('CTUN', ['Alt', 'DAlt'], 400);
    assert.strictEqual(ser.length, 2);
    assert.ok(ser[0].t.length <= 400, 'thinned to ' + ser[0].t.length);
    assert.ok(Math.abs(Math.max.apply(null, Array.from(ser[0].y)) - 30) < 0.01, 'spike kept by min/max thinning');
    const full = W.series('BAT', ['Volt'], 5000);
    assert.strictEqual(full[0].t.length, 179);
    assert.throws(() => W.series('NOPE', ['X'], 100), /No NOPE messages/);

    assert.strictEqual(W.severityOf('PreArm: Compass not calibrated'), 'warning');
    assert.strictEqual(W.severityOf('Crash: Disarming'), 'error');
    assert.strictEqual(W.severityOf('EKF3 IMU0 is using GPS'), 'info');

    // not a log at all
    const junk = new Uint8Array(4096).fill(7).buffer;
    const jl = W.index(junk);
    assert.strictEqual(Object.keys(jl.byName).length, 0);
    console.log('LOGVIEWER OK');
}

if (require.main === module) run();
module.exports = { makeLog: makeLog };
