// Node test for static/spotfinder.js: renders made-up ground (blotches like paving, gravel and
// shadows) as the Solo's downward GoPro would see it on the climb out and on the way back down,
// with GPS and height errors, other headings, other light and a few changes on the ground, and
// checks where the takeoff spot finder puts the spot. Run: node tools/test_spotfinder.js
// Prints "SPOTFINDER OK" and exits 0 when every case passes (test_suite.py test_42 runs it).
'use strict';
const path = require('path');
const PF = require(path.join(__dirname, '..', 'static', 'padfinder.js'));
const SF = require(path.join(__dirname, '..', 'static', 'spotfinder.js'));

let seed = 4242;
function rand() {                         // deterministic noise
    seed = (seed * 1103515245 + 12345) & 0x7fffffff;
    return seed / 0x7fffffff;
}

// The ground: a raster 2 cm a pixel, 50 m across, centred on the takeoff spot (north up)
const GRES = 0.02, GHALF = 25, GN = Math.round(2 * GHALF / GRES);

function makeGround(kind) {
    const g = new Float32Array(GN * GN).fill(110);
    if (kind === 'plain') return g;
    if (kind === 'tiles') {                // 50 cm tiles with dark joints
        for (let r = 0; r < GN; r++) {
            for (let c = 0; c < GN; c++) {
                const a = (r * GRES) % 0.5, b = (c * GRES) % 0.5;
                g[r * GN + c] = (a < 0.04 || b < 0.04) ? 60 : 150;
            }
        }
        return g;
    }
    // blotches from 8 cm to 2.5 m, lighter and darker, soft edged
    for (let k = 0; k < 2500; k++) {
        const cr = rand() * GN, cc = rand() * GN;
        const rad = Math.exp(Math.log(0.08) + rand() * Math.log(2.5 / 0.08)) / GRES;
        const amp = (rand() < 0.5 ? -1 : 1) * (15 + rand() * 45) * (rad * GRES < 0.3 ? 0.7 : 1);
        const r0 = Math.max(0, Math.floor(cr - rad - 2)), r1 = Math.min(GN - 1, Math.ceil(cr + rad + 2));
        const c0 = Math.max(0, Math.floor(cc - rad - 2)), c1 = Math.min(GN - 1, Math.ceil(cc + rad + 2));
        for (let r = r0; r <= r1; r++) {
            for (let c = c0; c <= c1; c++) {
                const d = Math.sqrt((r - cr) * (r - cr) + (c - cc) * (c - cc)) - rad;
                if (d < 2) g[r * GN + c] += amp * (d <= -2 ? 1 : (2 - d) / 4);
            }
        }
    }
    return g;
}

function groundAt(g, n, e) {
    const c = (e + GHALF) / GRES - 0.5, r = (GHALF - n) / GRES - 0.5;
    if (c < 0 || r < 0 || c >= GN - 1 || r >= GN - 1) return 110;
    const c0 = c | 0, r0 = r | 0, fc = c - c0, fr = r - r0, k = r0 * GN + c0;
    return (g[k] * (1 - fc) + g[k + 1] * fc) * (1 - fr) + (g[k + GN] * (1 - fc) + g[k + GN + 1] * fc) * fr;
}

/**
 * One w x h grey video frame. s: {height (m, true), n, e (the Solo, m from the takeoff spot),
 * yaw (deg), hfov, lens, gain, offset, noise, blur, box: [n, e, sizeN, sizeE, grey] laid on the ground}
 */
function frame(g, w, h, s) {
    const cam = PF.camera(w, h, s.hfov || 118, s.lens);
    const gray = new Uint8Array(w * h);
    const y = s.yaw * Math.PI / 180, cy = Math.cos(y), sy = Math.sin(y);
    for (let py = 0; py < h; py++) {
        for (let px = 0; px < w; px++) {
            const r = PF.ray(px, py, cam);
            let val = 140;                                     // sky / horizon: never used
            if (r[2] > 0.1) {
                const rgt = r[0] / r[2] * s.height, fwd = -r[1] / r[2] * s.height;
                const n = s.n + fwd * cy - rgt * sy, e = s.e + fwd * sy + rgt * cy;
                val = groundAt(g, n, e);
                const b = s.box;
                if (b && Math.abs(n - b[0]) < b[2] / 2 && Math.abs(e - b[1]) < b[3] / 2) val = b[4];
            }
            val = val * (s.gain || 1) + (s.offset || 0) + (rand() - 0.5) * 2 * (s.noise || 0);
            gray[py * w + px] = Math.max(0, Math.min(255, Math.round(val)));
        }
    }
    for (let pass = 0; pass < (s.blur || 0); pass++) {
        const src = gray.slice();
        for (let yy = 1; yy < h - 1; yy++) {
            for (let xx = 1; xx < w - 1; xx++) {
                let sum = 0;
                for (let dy = -1; dy <= 1; dy++) for (let dx = -1; dx <= 1; dx++) sum += src[(yy + dy) * w + xx + dx];
                gray[yy * w + xx] = Math.round(sum / 9);
            }
        }
    }
    return { gray: gray, cam: cam, w: w, h: h };
}

let failures = 0;
function check(name, cond, detail) {
    if (!cond) failures++;
    console.log((cond ? 'ok   ' : 'FAIL ') + name + (detail ? ': ' + detail : ''));
}

const W = 960, H = 540;

// The climb: pictures at 2, 4 and 8 m, drifting a little, heading 30 deg
function climb(g, opts) {
    const refs = [], reasons = [];
    [[2, 0.1, -0.15], [4, 0.3, -0.2], [8, 0.6, 0.4]].forEach(function(c) {
        const f = frame(g, W, H, Object.assign({ height: c[0], n: c[1], e: c[2], yaw: 30, noise: 4, blur: 1 }, opts || {}));
        const pyr = SF.pyramid(f.gray, W, H);
        // GPS on the climb is good to a few cm over these few seconds
        const res = SF.reference(pyr, f.cam, { height: c[0], yaw: 30 }, -c[1] + 0.03, -c[2] - 0.02);
        if (res.ok) refs.push(res.ref);
        else reasons.push(c[0] + ' m: ' + res.reason);
    });
    return { refs: refs, reasons: reasons };
}

/**
 * The way down: the Solo at (n, e) from the spot at true height, telemetry height off by
 * heightErr (x), GPS off by gpsErr (m north, east). Expect the spot within tol metres (null: no match).
 */
function landCase(name, g, refs, s, expect) {
    const f = frame(g, s.w || W, s.h || H, s);
    const t0 = Date.now();
    const pyr = SF.pyramid(f.gray, f.w, f.h);
    const view = { height: s.height * (s.heightErr || 1), yaw: s.yaw };
    const prior = { n: -s.n + (s.gpsErr ? s.gpsErr[0] : 0), e: -s.e + (s.gpsErr ? s.gpsErr[1] : 0),
                    radius: s.radius || 6, scale: 1 };
    const m = SF.match(pyr, f.cam, view, refs, prior);
    const ms = Date.now() - t0;
    if (expect === null) {
        check(name + ' (no match reported)', !m.ok, m.ok ? 'matched at ' + m.n.toFixed(2) + ', ' + m.e.toFixed(2) +
              ' score ' + m.score.toFixed(2) : m.reason + ', score ' + (m.score || 0).toFixed(2));
        return;
    }
    if (!m.ok) {
        check(name, false, m.reason + ', score ' + (m.score || 0).toFixed(2) + ' next ' + (m.second || 0).toFixed(2));
        return;
    }
    const err = Math.hypot(m.n + s.n, m.e + s.e);
    // the angle ArduPilot gets vs the true one
    const y = s.yaw * Math.PI / 180;
    const fwd = -s.n * Math.cos(y) - s.e * Math.sin(y), rgt = s.n * Math.sin(y) - s.e * Math.cos(y);
    const aErr = Math.max(Math.abs(m.angle_x - Math.atan2(rgt, s.height)), Math.abs(m.angle_y - Math.atan2(-fwd, s.height)));
    check(name, err < expect.tol, 'spot off by ' + err.toFixed(2) + ' m, angle error ' + aErr.toFixed(3) +
          ' rad, score ' + m.score.toFixed(2) + ' (next ' + m.second.toFixed(2) + '), height ' + m.below.toFixed(2) +
          ' m (true ' + s.height + '), template ' + m.refHeight + ' m, ' + ms + ' ms');
}

const ground = makeGround('blotches');
const c = climb(ground);
check('climb: three pictures saved', c.refs.length === 3, c.reasons.join('; '));

landCase('20 m, 4 m away, GPS 1.5 m off, other heading', ground, c.refs,
         { height: 20, n: 3, e: -2.5, yaw: 200, noise: 4, blur: 1, gpsErr: [1.2, -0.9] }, { tol: 0.4 });
landCase('12 m, height reads 12% high, dimmer light', ground, c.refs,
         { height: 12, n: -1.5, e: 1.0, yaw: 120, noise: 5, blur: 1, heightErr: 1.12, gain: 0.7, offset: 10, gpsErr: [-1, 1] }, { tol: 0.3 });
landCase('6 m, a bag left 1 m from the spot', ground, c.refs,
         { height: 6, n: 0.8, e: 0.6, yaw: 75, noise: 4, blur: 1, gpsErr: [0.6, 0.4], box: [1.0, 0.2, 0.5, 0.35, 30] }, { tol: 0.2 });
landCase('5 m, tracking (small search)', ground, c.refs,
         { height: 5, n: -0.4, e: 0.3, yaw: 300, noise: 4, blur: 1, gpsErr: [0.25, -0.2], radius: 1.5 }, { tol: 0.15 });
landCase('3 m', ground, c.refs,
         { height: 3, n: 0.25, e: -0.2, yaw: 10, noise: 4, blur: 1, gpsErr: [0.15, 0.1], radius: 1.0 }, { tol: 0.1 });
landCase('1.6 m, almost down', ground, c.refs,
         { height: 1.6, n: -0.1, e: 0.15, yaw: 45, noise: 4, blur: 1, gpsErr: [0.1, 0.1], radius: 0.6 }, { tol: 0.08 });
landCase('1280 x 720 video, 15 m', ground, c.refs,
         { w: 1280, h: 720, height: 15, n: 2, e: 2, yaw: 160, noise: 4, blur: 1, gpsErr: [-1.5, 1] }, { tol: 0.35 });
landCase('0.8 m: below the lowest picture', ground, c.refs,
         { height: 0.8, n: 0, e: 0, yaw: 30, noise: 4, gpsErr: [0, 0], radius: 0.5 }, null);

// Somewhere else entirely: must not match
seed = 999;
const elsewhere = makeGround('blotches');
landCase('a different place', elsewhere, c.refs, { height: 10, n: 0, e: 0, yaw: 30, noise: 4, blur: 1 }, null);
landCase('a different place, 4 m', elsewhere, c.refs, { height: 4, n: 0.3, e: 0, yaw: 90, noise: 4, blur: 1, radius: 2 }, null);

// Ground that can't be recognised: nothing saved
const plain = climb(makeGround('plain'));
check('plain ground: no pictures saved', plain.refs.length === 0, plain.reasons.join('; '));
const tiles = climb(makeGround('tiles'));
check('even tiles: no pictures saved', tiles.refs.length === 0, tiles.reasons.join('; '));

if (failures) {
    console.log(failures + ' SPOTFINDER CASE(S) FAILED');
    process.exit(1);
}
console.log('SPOTFINDER OK');
