// Node test for static/padfinder.js: renders the landing pad on the ground as the Solo's
// downward GoPro would see it (fisheye or rectilinear, any height, offset and turn, noise,
// uneven light) and checks what the pad finder reports. Run: node tools/test_padfinder.js
// Prints "PADFINDER OK" and exits 0 when every case passes (test_suite.py test_41 runs it).
'use strict';
const path = require('path');
const PF = require(path.join(__dirname, '..', 'static', 'padfinder.js'));

let seed = 12345;
function rand() {                         // deterministic noise
    seed = (seed * 1103515245 + 12345) & 0x7fffffff;
    return seed / 0x7fffffff;
}

// Pad colour at ground point (gx right, gy back/down-image) in pad coordinates; null = not the pad
function padValue(px, py, mirror) {
    // px, py in cells from the pad's top-left corner, 0..6 is the black square, 1-cell white margin
    if (px < -1 || py < -1 || px >= 7 || py >= 7) return null;
    if (px < 0 || py < 0 || px >= 6 || py >= 6) return 1;                  // margin: white
    const i = Math.floor(px), j = Math.floor(py);
    if (i === 0 || j === 0 || i === 5 || j === 5) return 0;               // border: black
    const c = mirror ? 3 - (i - 1) : i - 1;
    return PF.PATTERN[j - 1][c];
}

/**
 * Render a w x h grey frame. scene: {height (m), x, y (pad centre on the ground, m: right, back),
 * turn (deg, clockwise on screen), size (m, black square), hfov, lens, noise, gradient, mirror, nopad}
 */
function render(w, h, s) {
    const cam = PF.camera(w, h, s.hfov, s.lens);
    const gray = new Uint8Array(w * h);
    const t = (s.turn || 0) * Math.PI / 180, ct = Math.cos(t), st = Math.sin(t);
    const cellM = s.size / 6;
    // a few dark blobs and textured ground, like grass and shadows
    const blobs = [];
    for (let k = 0; k < 12; k++) blobs.push([rand() * w, rand() * h, 6 + rand() * 30]);
    const SS = 2;
    for (let y = 0; y < h; y++) {
        for (let x = 0; x < w; x++) {
            let acc = 0;
            for (let sy = 0; sy < SS; sy++) {
                for (let sx = 0; sx < SS; sx++) {
                    const r = PF.ray(x + (sx + 0.5) / SS - 0.5, y + (sy + 0.5) / SS - 0.5, cam);
                    let v = null;
                    if (!s.nopad && r[2] > 0.05) {
                        const gx = r[0] / r[2] * s.height - s.x, gy = r[1] / r[2] * s.height - s.y;
                        // into the pad's own (turned) frame, origin at the black square's top-left
                        const lx = (ct * gx + st * gy) / cellM + 3, ly = (-st * gx + ct * gy) / cellM + 3;
                        v = padValue(lx, ly, s.mirror);
                    }
                    if (v === null) {
                        let g = 95 + 25 * Math.sin(x * 0.37) * Math.sin(y * 0.23);
                        for (let b = 0; b < blobs.length; b++) {
                            const dx = x - blobs[b][0], dy = y - blobs[b][1];
                            if (dx * dx + dy * dy < blobs[b][2] * blobs[b][2]) g = 40;
                        }
                        acc += g;
                    } else {
                        acc += v ? 215 : 30;
                    }
                }
            }
            let val = acc / (SS * SS);
            if (s.gradient) val *= 0.55 + 0.45 * x / w;                       // sun on one side, shade on the other
            val += (rand() - 0.5) * 2 * (s.noise || 0);
            gray[y * w + x] = Math.max(0, Math.min(255, Math.round(val)));
        }
    }
    for (let pass = 0; pass < (s.blur || 0); pass++) {                         // video compression softness
        const src = gray.slice();
        for (let y = 1; y < h - 1; y++) {
            for (let x = 1; x < w - 1; x++) {
                let sum = 0;
                for (let dy = -1; dy <= 1; dy++) for (let dx = -1; dx <= 1; dx++) sum += src[(y + dy) * w + x + dx];
                gray[y * w + x] = Math.round(sum / 9);
            }
        }
    }
    return { gray: gray, cam: cam };
}

// Where a ground point lands in the image (inverse of PF.ray)
function project(gx, gy, height, cam) {
    const len = Math.sqrt(gx * gx + gy * gy + height * height);
    const v = [gx / len, gy / len, height / len];
    if (cam.fisheye) {
        const theta = Math.acos(v[2]), rr = Math.sqrt(v[0] * v[0] + v[1] * v[1]) || 1e-9;
        return [cam.cx + cam.f * theta * v[0] / rr, cam.cy + cam.f * theta * v[1] / rr];
    }
    return [cam.cx + cam.f * v[0] / v[2], cam.cy + cam.f * v[1] / v[2]];
}

let failures = 0;
function check(name, cond, detail) {
    if (!cond) failures++;
    console.log((cond ? 'ok   ' : 'FAIL ') + name + (detail ? ': ' + detail : ''));
}

function runCase(name, w, h, s, expect) {
    const img = render(w, h, s);
    const t0 = Date.now();
    const det = PF.detect(img.gray, w, h);
    const ms = Date.now() - t0;
    if (expect === null) {
        check(name + ' (no pad reported)', det === null, det ? 'found one at ' + det.center : '');
        return;
    }
    if (!det) { check(name, false, 'pad not found'); return; }
    const m = PF.measure(det, img.cam, s.size);
    const trueDist = Math.sqrt(s.x * s.x + s.y * s.y + s.height * s.height);
    const ax = Math.atan2(s.x, s.height), ay = Math.atan2(s.y, s.height);
    const dErr = Math.abs(m.distance - trueDist) / trueDist;
    const aErr = Math.max(Math.abs(m.angle_x - ax), Math.abs(m.angle_y - ay));
    // the pattern's top-left corner of the black square, turned with the pad
    const t = (s.turn || 0) * Math.PI / 180, half = s.size / 2;
    const tlx = s.x + Math.cos(t) * -half - Math.sin(t) * -half;
    const tly = s.y + Math.sin(t) * -half + Math.cos(t) * -half;
    const tl = project(tlx, tly, s.height, img.cam);
    const cErr = Math.hypot(det.corners[0][0] - tl[0], det.corners[0][1] - tl[1]);
    check(name, dErr < (expect.dist || 0.04) && aErr < (expect.angle || 0.01) && cErr < Math.max(2.5, det.side * 0.08),
          'distance ' + m.distance.toFixed(2) + ' m (true ' + trueDist.toFixed(2) + '), angle error ' +
          aErr.toFixed(4) + ' rad, top-left corner off by ' + cErr.toFixed(1) + ' px, side ' + det.side.toFixed(1) +
          ' px, ' + ms + ' ms');
}

const PAD = 0.6096;   // 24 in
const W = 960, H = 540;
runCase('5 m, centred, square on', W, H, { height: 5, x: 0, y: 0, turn: 0, size: PAD, hfov: 118, noise: 6 }, {});
runCase('8 m, off to one side, turned 30 deg', W, H, { height: 8, x: 1.6, y: -1.1, turn: 30, size: PAD, hfov: 118, noise: 8 }, {});
runCase('3 m, turned 180 deg', W, H, { height: 3, x: -0.4, y: 0.5, turn: 180, size: PAD, hfov: 118, noise: 6 }, {});
runCase('2 m, turned 45 deg, uneven light', W, H, { height: 2, x: 0.2, y: 0.1, turn: 45, size: PAD, hfov: 118, noise: 6, gradient: true }, {});
runCase('12 m, small in the frame', W, H, { height: 12, x: 0.5, y: 0.8, turn: 75, size: PAD, hfov: 118, noise: 5 }, { dist: 0.08, angle: 0.015 });
runCase('1 m, pad fills most of the frame', W, H, { height: 1.0, x: 0.05, y: -0.05, turn: 10, size: PAD, hfov: 118, noise: 6 }, {});
runCase('rectilinear lens, 6 m', W, H, { height: 6, x: -1.0, y: 0.6, turn: 120, size: PAD, hfov: 90, lens: 'rectilinear', noise: 6 }, {});
runCase('1280 x 720 video, 10 m', 1280, 720, { height: 10, x: 2.0, y: 1.0, turn: 200, size: PAD, hfov: 118, noise: 7 }, { dist: 0.06 });
runCase('soft video (blurred), 6 m', W, H, { height: 6, x: 0.8, y: -0.6, turn: 15, size: PAD, hfov: 118, noise: 6, blur: 1 }, { dist: 0.05 });
runCase('soft video, 10 m', W, H, { height: 10, x: -0.5, y: 0.4, turn: 300, size: PAD, hfov: 118, noise: 5, blur: 1 }, { dist: 0.08, angle: 0.015 });
runCase('15 m, at the edge of the range', W, H, { height: 15, x: 0.3, y: -0.2, turn: 5, size: PAD, hfov: 118, noise: 4 }, { dist: 0.1, angle: 0.02 });
runCase('no pad, just ground and shadows', W, H, { height: 5, x: 0, y: 0, size: PAD, hfov: 118, noise: 8, nopad: true }, null);
runCase('pad printed mirrored', W, H, { height: 4, x: 0, y: 0, turn: 0, size: PAD, hfov: 118, noise: 6, mirror: true }, null);
runCase('pad cut off by the frame edge', W, H, { height: 3, x: 4.95, y: 0, turn: 0, size: PAD, hfov: 118, noise: 6 }, null);

if (failures) {
    console.log(failures + ' PADFINDER CASE(S) FAILED');
    process.exit(1);
}
console.log('PADFINDER OK');
