/**
 * 🎯 Landing pad finder for the Solo Cockpit (no libraries).
 *
 * The pad is a black square of 6 x 6 cells on a white margin: the outer ring of cells is black,
 * the inner 4 x 4 cells carry PATTERN (1 = white). No rotation or mirror image of the pattern
 * looks like another (they differ in at least 8 cells), so it also tells which way the pad is turned.
 * static/landing_pad.html prints it.
 *
 *   detect(gray, w, h)          -> the pad's corners and centre in pixels, or null
 *   camera(w, h, hfovDeg, lens) -> the camera model for those pixels
 *   measure(det, cam, padSizeM) -> LANDING_TARGET angles (rad) and distance (m) for ArduPilot
 *
 * Camera axes: x right, y down (image), z out of the lens. With the gimbal pointing straight
 * down, image up is the Solo's forward, so ArduPilot's (forward, right) = (-y, x).
 * Works in the browser (window.PadFinder) and in Node (module.exports) for the tests.
 */
(function(root) {
    'use strict';

    const PATTERN = [
        [1, 1, 1, 0],
        [0, 1, 1, 1],
        [1, 0, 0, 0],
        [1, 0, 0, 0]
    ];
    const GRID = 6;                     // cells across the black square (border + 4 + border)
    const MAX_BIT_ERRORS = 2;           // rotations and mirrors differ in >= 8 cells: 2 wrong cells are safe
    const MIN_CONTRAST = 25;            // grey levels between the black border and the white margin

    function rotateCW(bits) {
        const n = bits.length;
        const out = [];
        for (let r = 0; r < n; r++) {
            out.push([]);
            for (let c = 0; c < n; c++) out[r].push(bits[n - 1 - c][r]);
        }
        return out;
    }

    const ROTATIONS = (function() {
        const list = [PATTERN];
        for (let k = 1; k < 4; k++) list.push(rotateCW(list[k - 1]));
        return list;
    })();

    function toGray(rgba, w, h, out) {
        const n = w * h;
        const g = (out && out.length === n) ? out : new Uint8Array(n);
        for (let i = 0, j = 0; i < n; i++, j += 4) {
            g[i] = (rgba[j] * 77 + rgba[j + 1] * 150 + rgba[j + 2] * 29) >> 8;
        }
        return g;
    }

    // Work buffers, kept between frames of the same size (10 frames a second on a phone)
    let bufKey = '';
    let bufs = null;
    function buffers(w, h) {
        const key = w + 'x' + h;
        if (key !== bufKey) {
            bufKey = key;
            bufs = {
                integ: new Uint32Array((w + 1) * (h + 1)),
                mask: new Uint8Array(w * h),
                label: new Uint8Array(w * h),
                stack: new Int32Array(w * h)
            };
        }
        return bufs;
    }

    // Dark pixels: darker than the mean of a large window around them (copes with sun and shade)
    function darkMask(gray, w, h, win, c) {
        const W = w + 1;
        const integ = buffers(w, h).integ;
        for (let y = 0; y < h; y++) {
            let rowSum = 0;
            for (let x = 0; x < w; x++) {
                rowSum += gray[y * w + x];
                integ[(y + 1) * W + x + 1] = integ[y * W + x + 1] + rowSum;
            }
        }
        const mask = buffers(w, h).mask;
        mask.fill(0);
        const r = win >> 1;
        for (let y = 0; y < h; y++) {
            const y0 = Math.max(0, y - r), y1 = Math.min(h, y + r + 1);
            for (let x = 0; x < w; x++) {
                const x0 = Math.max(0, x - r), x1 = Math.min(w, x + r + 1);
                const area = (x1 - x0) * (y1 - y0);
                const sum = integ[y1 * W + x1] - integ[y0 * W + x1] - integ[y1 * W + x0] + integ[y0 * W + x0];
                if (gray[y * w + x] * area < sum - c * area) mask[y * w + x] = 1;
            }
        }
        return mask;
    }

    // Connected dark regions big enough to be the pad, with their extreme points
    function regions(mask, w, h, minSide) {
        const label = buffers(w, h).label;
        label.fill(0);
        const stack = buffers(w, h).stack;
        const found = [];
        for (let start = 0; start < w * h; start++) {
            if (!mask[start] || label[start]) continue;
            let sp = 0;
            stack[sp++] = start;
            label[start] = 1;
            let count = 0, minx = w, maxx = -1, miny = h, maxy = -1;
            // extreme points: x+y, x-y, y-x, -x-y (diagonals) and top/right/bottom/left
            let sPP = -1e9, sPM = -1e9, sMP = -1e9, sMM = -1e9;
            let pPP = 0, pPM = 0, pMP = 0, pMM = 0, pT = 0, pR = 0, pB = 0, pL = 0;
            while (sp > 0) {
                const p = stack[--sp];
                const x = p % w, y = (p - x) / w;
                count++;
                if (y < miny) { miny = y; pT = p; }
                if (x > maxx) { maxx = x; pR = p; }
                if (y > maxy) { maxy = y; pB = p; }
                if (x < minx) { minx = x; pL = p; }
                if (x + y > sPP) { sPP = x + y; pPP = p; }
                if (x - y > sPM) { sPM = x - y; pPM = p; }
                if (y - x > sMP) { sMP = y - x; pMP = p; }
                if (-x - y > sMM) { sMM = -x - y; pMM = p; }
                if (x > 0 && mask[p - 1] && !label[p - 1]) { label[p - 1] = 1; stack[sp++] = p - 1; }
                if (x < w - 1 && mask[p + 1] && !label[p + 1]) { label[p + 1] = 1; stack[sp++] = p + 1; }
                if (y > 0 && mask[p - w] && !label[p - w]) { label[p - w] = 1; stack[sp++] = p - w; }
                if (y < h - 1 && mask[p + w] && !label[p + w]) { label[p + w] = 1; stack[sp++] = p + w; }
            }
            const bw = maxx - minx + 1, bh = maxy - miny + 1;
            if (bw < minSide || bh < minSide || count < 4 * minSide) continue;
            if (minx < 2 || miny < 2 || maxx > w - 3 || maxy > h - 3) continue;   // cut off by the frame edge
            if (Math.max(bw, bh) > 4 * Math.min(bw, bh)) continue;
            const pt = function(p) { return [p % w, Math.floor(p / w)]; };
            found.push({
                bw: bw, bh: bh,
                diag: [pt(pMM), pt(pPM), pt(pPP), pt(pMP)],    // TL, TR, BR, BL for a square
                axis: [pt(pT), pt(pR), pt(pB), pt(pL)]         // the same for a square turned 45 degrees
            });
        }
        found.sort(function(a, b) { return b.bw * b.bh - a.bw * a.bh; });
        return found;
    }

    function quadArea(q) {
        let s = 0;
        for (let i = 0; i < 4; i++) {
            const a = q[i], b = q[(i + 1) % 4];
            s += a[0] * b[1] - b[0] * a[1];
        }
        return s / 2;            // > 0: clockwise on screen (y down)
    }

    // Unit square (0,0) (1,0) (1,1) (0,1) -> the quad's corners q[0..3]
    function squareToQuad(q) {
        const x0 = q[0][0], y0 = q[0][1], x1 = q[1][0], y1 = q[1][1];
        const x2 = q[2][0], y2 = q[2][1], x3 = q[3][0], y3 = q[3][1];
        const dx1 = x1 - x2, dx2 = x3 - x2, dx3 = x0 - x1 + x2 - x3;
        const dy1 = y1 - y2, dy2 = y3 - y2, dy3 = y0 - y1 + y2 - y3;
        let g = 0, hh = 0;
        const den = dx1 * dy2 - dx2 * dy1;
        if ((Math.abs(dx3) > 1e-9 || Math.abs(dy3) > 1e-9) && Math.abs(den) > 1e-12) {
            g = (dx3 * dy2 - dx2 * dy3) / den;
            hh = (dx1 * dy3 - dx3 * dy1) / den;
        }
        const a = x1 - x0 + g * x1, b = x3 - x0 + hh * x3, c = x0;
        const d = y1 - y0 + g * y1, e = y3 - y0 + hh * y3, f = y0;
        return function(u, v) {
            const s = g * u + hh * v + 1;
            return [(a * u + b * v + c) / s, (d * u + e * v + f) / s];
        };
    }

    function bilinear(gray, w, h, x, y) {
        if (x < 0 || y < 0 || x > w - 1 || y > h - 1) return -1;
        const x0 = Math.floor(x), y0 = Math.floor(y);
        const x1 = Math.min(x0 + 1, w - 1), y1 = Math.min(y0 + 1, h - 1);
        const fx = x - x0, fy = y - y0;
        const top = gray[y0 * w + x0] * (1 - fx) + gray[y0 * w + x1] * fx;
        const bot = gray[y1 * w + x0] * (1 - fx) + gray[y1 * w + x1] * fx;
        return top * (1 - fy) + bot * fy;
    }

    function median(list) {
        const s = list.slice().sort(function(a, b) { return a - b; });
        return s[s.length >> 1];
    }

    // Read the 6 x 6 grid inside the quad. -> {rotation, errors, contrast} or null
    function readGrid(gray, w, h, quad) {
        const map = squareToQuad(quad);
        const cell = 1 / GRID;
        const sample = function(i, j) {          // average of 5 points inside cell (i = column, j = row)
            const u = (i + 0.5) * cell, v = (j + 0.5) * cell, d = 0.2 * cell;
            let sum = 0;
            const pts = [[0, 0], [d, d], [-d, d], [d, -d], [-d, -d]];
            for (let k = 0; k < pts.length; k++) {
                const p = map(u + pts[k][0], v + pts[k][1]);
                const val = bilinear(gray, w, h, p[0], p[1]);
                if (val < 0) return -1;
                sum += val;
            }
            return sum / pts.length;
        };
        const border = [];
        const inner = [];
        for (let j = 0; j < GRID; j++) {
            for (let i = 0; i < GRID; i++) {
                const val = sample(i, j);
                if (val < 0) return null;
                if (i === 0 || j === 0 || i === GRID - 1 || j === GRID - 1) border.push(val);
                else inner.push({ i: i - 1, j: j - 1, val: val });
            }
        }
        // the white margin, half a cell outside the black square
        const margin = [];
        for (let k = 0; k < GRID; k++) {
            const t = (k + 0.5) * cell;
            const pts = [map(t, -0.5 * cell), map(1 + 0.5 * cell, t), map(t, 1 + 0.5 * cell), map(-0.5 * cell, t)];
            for (let m = 0; m < 4; m++) {
                const val = bilinear(gray, w, h, pts[m][0], pts[m][1]);
                if (val < 0) return null;
                margin.push(val);
            }
        }
        const dark = median(border), white = median(margin);
        const contrast = white - dark;
        if (contrast < MIN_CONTRAST) return null;
        const thr = (dark + white) / 2;
        let lightBorder = 0;
        for (let k = 0; k < border.length; k++) if (border[k] > thr) lightBorder++;
        if (lightBorder > 1) return null;
        let lightMargin = 0;
        for (let k = 0; k < margin.length; k++) if (margin[k] > thr) lightMargin++;
        if (lightMargin < margin.length - 2) return null;
        const bits = [[0, 0, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0]];
        for (let k = 0; k < inner.length; k++) bits[inner[k].j][inner[k].i] = inner[k].val > thr ? 1 : 0;
        let best = -1, bestErr = 99;
        for (let r = 0; r < 4; r++) {
            let err = 0;
            for (let j = 0; j < 4; j++) for (let i = 0; i < 4; i++) if (bits[j][i] !== ROTATIONS[r][j][i]) err++;
            if (err < bestErr) { bestErr = err; best = r; }
        }
        if (bestErr > MAX_BIT_ERRORS) return null;
        return { rotation: best, errors: bestErr, contrast: Math.round(contrast) };
    }

    /**
     * Find the pad in a grey image. -> null or
     * {corners: [[x, y] x 4] (the pattern's top-left, top-right, bottom-right, bottom-left),
     *  center: [x, y], side: mean side in pixels, rotation, errors, contrast}
     */
    function detect(gray, w, h, opts) {
        opts = opts || {};
        const minSide = opts.minSide || 12;
        let win = opts.window || Math.round(Math.min(w, h) / 6);
        win = Math.max(15, win | 1);
        const mask = darkMask(gray, w, h, win, opts.darkBias || 7);
        const cands = regions(mask, w, h, minSide);
        const maxTries = opts.maxCandidates || 40;
        for (let n = 0; n < cands.length && n < maxTries; n++) {
            const cd = cands[n];
            const aDiag = Math.abs(quadArea(cd.diag)), aAxis = Math.abs(quadArea(cd.axis));
            let quad = (aDiag >= aAxis ? cd.diag : cd.axis).map(function(p) { return [p[0], p[1]]; });
            const area = Math.max(aDiag, aAxis);
            if (area < 0.35 * cd.bw * cd.bh) continue;
            if (quadArea(quad) < 0) quad.reverse();
            // the extreme pixels' centres sit half a pixel inside the true corners
            const cx = (quad[0][0] + quad[1][0] + quad[2][0] + quad[3][0]) / 4;
            const cy = (quad[0][1] + quad[1][1] + quad[2][1] + quad[3][1]) / 4;
            quad = quad.map(function(p) {
                const dx = p[0] - cx, dy = p[1] - cy, len = Math.sqrt(dx * dx + dy * dy) || 1;
                return [p[0] + 0.7 * dx / len, p[1] + 0.7 * dy / len];
            });
            const sides = [];
            for (let i = 0; i < 4; i++) {
                const a = quad[i], b = quad[(i + 1) % 4];
                sides.push(Math.sqrt((a[0] - b[0]) * (a[0] - b[0]) + (a[1] - b[1]) * (a[1] - b[1])));
            }
            if (Math.min.apply(null, sides) < 0.5 * Math.max.apply(null, sides)) continue;
            const grid = readGrid(gray, w, h, quad);
            if (!grid) continue;
            // corner k of the quad is the pattern's top-left after k clockwise turns
            const corners = [];
            for (let i = 0; i < 4; i++) corners.push(quad[(grid.rotation + i) % 4]);
            const center = squareToQuad(quad)(0.5, 0.5);
            return {
                corners: corners, center: center,
                side: (sides[0] + sides[1] + sides[2] + sides[3]) / 4,
                rotation: grid.rotation, errors: grid.errors, contrast: grid.contrast
            };
        }
        return null;
    }

    /** Camera model for a w x h image. lens: 'fisheye' (equidistant, GoPro) or 'rectilinear'. */
    function camera(w, h, hfovDeg, lens) {
        const half = (hfovDeg || 118) * Math.PI / 360;
        const fisheye = lens !== 'rectilinear';
        return { w: w, h: h, cx: w / 2, cy: h / 2, fisheye: fisheye,
                 f: fisheye ? (w / 2) / half : (w / 2) / Math.tan(half) };
    }

    /** Unit vector out of the camera through pixel (x, y). */
    function ray(x, y, cam) {
        const dx = x - cam.cx, dy = y - cam.cy;
        if (cam.fisheye) {
            const r = Math.sqrt(dx * dx + dy * dy);
            if (r < 1e-9) return [0, 0, 1];
            const theta = Math.min(r / cam.f, Math.PI - 1e-3);
            const s = Math.sin(theta) / r;
            return [dx * s, dy * s, Math.cos(theta)];
        }
        const len = Math.sqrt(dx * dx + dy * dy + cam.f * cam.f);
        return [dx / len, dy / len, cam.f / len];
    }

    /**
     * What ArduPilot needs from one sighting (LANDING_TARGET, MAV_FRAME_LOCAL_FRD) plus a ground
     * picture for the pilot: {angle_x, angle_y, distance, size (rad), ahead, right, below (m)}.
     * The gimbal holds the camera level and pointing down, so the pad lies flat across the view:
     * each corner's ray meets the ground at (x/z, y/z) x height, and the pad's printed size gives
     * the height. The distance is along the ray to the pad's centre.
     */
    function measure(det, cam, padSizeM) {
        const rays = det.corners.map(function(p) { return ray(p[0], p[1], cam); });
        const flat = rays.map(function(r) { return [r[0] / r[2], r[1] / r[2]]; });
        let sideSum = 0, angSum = 0;
        for (let i = 0; i < 4; i++) {
            const a = flat[i], b = flat[(i + 1) % 4];
            sideSum += Math.sqrt((a[0] - b[0]) * (a[0] - b[0]) + (a[1] - b[1]) * (a[1] - b[1]));
            const ra = rays[i], rb = rays[(i + 1) % 4];
            angSum += Math.acos(Math.max(-1, Math.min(1, ra[0] * rb[0] + ra[1] * rb[1] + ra[2] * rb[2])));
        }
        const height = padSizeM / (sideSum / 4);
        const c = ray(det.center[0], det.center[1], cam);
        const dist = height / c[2];
        return {
            angle_x: Math.atan2(c[0], c[2]),
            angle_y: Math.atan2(c[1], c[2]),
            distance: dist,
            size: angSum / 4,
            ahead: -c[1] * dist,
            right: c[0] * dist,
            below: height
        };
    }

    const api = {
        PATTERN: PATTERN, GRID: GRID,
        toGray: toGray, detect: detect, camera: camera, ray: ray, measure: measure,
        squareToQuad: squareToQuad
    };
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
    else root.PadFinder = api;
})(this);
