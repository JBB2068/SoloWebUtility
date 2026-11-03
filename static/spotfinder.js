/**
 * 📍 Takeoff spot finder for the Solo Cockpit (no libraries).
 *
 * On the climb out, with the camera pointing straight down, reference() turns a video frame into a
 * small picture of the ground around the takeoff spot (the "template") at a known ground scale.
 * On the way back down, match() turns the live frame into a ground picture at the same scale and
 * turn, around where GPS says the spot is, and slides the template over it to find where the spot
 * really is. The heading and height come from telemetry, so the two pictures differ only by a
 * shift, plus a few % of scale for the height error (searched too).
 *
 *   pyramid(gray, w, h)                    -> the frame at 1, 1/2, 1/4 ... size (area averaged)
 *   reference(pyr, cam, view, homeN, homeE) -> {ok: true, ref} or {ok: false, reason}
 *   match(pyr, cam, view, refs, prior)       -> {ok: true, n, e, angle_x, ...} or {ok: false, reason}
 *   project(n, e, cam, view)                -> the pixel a ground point is seen at
 *
 * view: {height (m above the takeoff spot), yaw (deg)}. Ground offsets (n, e) are metres north and
 * east of the point straight below the Solo. cam is PadFinder.camera(): x right, y down, and with
 * the gimbal pointing straight down image up is the Solo's forward.
 * Works in the browser (window.SpotFinder) and in Node (module.exports) for the tests.
 */
(function(root) {
    'use strict';

    const COLS = 40;                    // template: pixels across (the Solo's left-right at the time)
    const ROWS = 24;                    // ... and fore-aft (the video is wider than it is tall)
    const GSD_PER_M = 0.03;             // template metres per pixel, per metre of height: 1.2 x 0.72 h
    const MAX_THETA = 52 * Math.PI / 180;   // ignore the frame's far sides (the Solo's legs, blur)
    const MIN_STD = 6;                  // grey levels: plainer ground than this can't be matched
    const SELF_RADIUS = 8;              // pixels searched around the template for look-alikes
    const MAX_SELF = 0.85;              // ... and how alike they may be (tiles, stripes, lawn)
    const PEAK_EXCLUDE = 4;             // pixels around the best match that belong to the same peak
    const MAX_SEARCH_PX = 24;           // search radius cap: ~7 M multiply-adds a look for 3 scales
    const MIN_SCORE = 0.6;              // correlation the best match needs
    const MIN_MARGIN = 0.1;             // ... and how far ahead of the next-best spot it must be
    const SCALE_STEP = 1.08;            // height error searched: x 1/1.08, 1, 1.08 around the last
    const MAX_REF_RATIO = 1.6;          // a template is used down to its height / 1.6

    // Area-averaged half sizes, kept between frames of the same size
    let pyrKey = '';
    let pyrBufs = [];
    function pyramid(gray, w, h) {
        const key = w + 'x' + h;
        if (key !== pyrKey) {
            pyrKey = key;
            pyrBufs = [];
            let lw = w, lh = h;
            while (lw >= 64 && lh >= 64) {
                lw >>= 1;
                lh >>= 1;
                pyrBufs.push(new Float32Array(lw * lh));
            }
        }
        const levels = [{ g: gray, w: w, h: h }];
        for (let k = 0; k < pyrBufs.length; k++) {
            const src = levels[k], s = src.g, sw = src.w;
            const lw = src.w >> 1, lh = src.h >> 1, out = pyrBufs[k];
            for (let y = 0; y < lh; y++) {
                const a = 2 * y * sw, b = a + sw;
                for (let x = 0; x < lw; x++) {
                    const i = 2 * x;
                    out[y * lw + x] = (s[a + i] + s[a + i + 1] + s[b + i] + s[b + i + 1]) * 0.25;
                }
            }
            levels.push({ g: out, w: lw, h: lh });
        }
        return levels;
    }

    // Pixel (level 0) of the ground point fwd metres ahead and rgt metres right, h below; null past MAX_THETA
    function bodyToImage(fwd, rgt, h, cam) {
        const rho = Math.sqrt(fwd * fwd + rgt * rgt);
        if (rho < 1e-9) return [cam.cx, cam.cy];
        const theta = Math.atan2(rho, h);
        if (theta > MAX_THETA) return null;
        const r = cam.fisheye ? cam.f * theta : cam.f * rho / h;
        return [cam.cx + r * rgt / rho, cam.cy - r * fwd / rho];
    }

    function project(n, e, cam, view) {
        const y = view.yaw * Math.PI / 180, c = Math.cos(y), s = Math.sin(y);
        return bodyToImage(n * c + e * s, -n * s + e * c, view.height, cam);
    }

    /**
     * A rows x cols ground picture centred (cN, cE) metres from below the Solo, gsd metres a pixel,
     * its "up" pointing at heading rot (deg). Pixels the camera doesn't see are NaN.
     */
    function render(pyr, cam, view, cN, cE, rot, gsd, rows, cols, out) {
        const h = view.height;
        const yaw = view.yaw * Math.PI / 180, cy = Math.cos(yaw), sy = Math.sin(yaw);
        const r0 = rot * Math.PI / 180, cr = Math.cos(r0), sr = Math.sin(r0);
        // the pyramid level whose pixels are about gsd on the ground straight below
        const foot = h / cam.f;
        let L = 0;
        while (L + 1 < pyr.length && foot * (1 << (L + 1)) <= gsd) L++;
        const lev = pyr[L], g = lev.g, lw = lev.w, lh = lev.h, inv = 1 / (1 << L);
        const hr = (rows - 1) / 2, hc = (cols - 1) / 2;
        for (let i = 0; i < rows; i++) {
            const u = (hr - i) * gsd;
            for (let j = 0; j < cols; j++) {
                const v = (j - hc) * gsd;
                const n = cN + u * cr - v * sr, e = cE + u * sr + v * cr;
                const p = bodyToImage(n * cy + e * sy, -n * sy + e * cy, h, cam);
                let val = NaN;
                if (p) {
                    const x = (p[0] + 0.5) * inv - 0.5, y = (p[1] + 0.5) * inv - 0.5;
                    if (x >= 0 && y >= 0 && x < lw - 1 && y < lh - 1) {
                        const x0 = x | 0, y0 = y | 0, fx = x - x0, fy = y - y0, k = y0 * lw + x0;
                        val = (g[k] * (1 - fx) + g[k + 1] * fx) * (1 - fy) + (g[k + lw] * (1 - fx) + g[k + lw + 1] * fx) * fy;
                    }
                }
                out[i * cols + j] = val;
            }
        }
        return out;
    }

    /**
     * Normalised cross-correlation of the zero-mean template t (rows x cols, norm tnorm) at every
     * place in map (mr x mc). Places that need a pixel the camera doesn't see, or plain ground, are NaN.
     */
    function correlate(map, mr, mc, t, tnorm, rows, cols) {
        const W = mc + 1;
        const s1 = new Float64Array((mr + 1) * W), s2 = new Float64Array((mr + 1) * W), bad = new Int32Array((mr + 1) * W);
        for (let i = 0; i < mr; i++) {
            let a1 = 0, a2 = 0, ab = 0;
            for (let j = 0; j < mc; j++) {
                const v = map[i * mc + j];
                if (v !== v) ab++;
                else { a1 += v; a2 += v * v; }
                const k = (i + 1) * W + j + 1;
                s1[k] = s1[k - W] + a1;
                s2[k] = s2[k - W] + a2;
                bad[k] = bad[k - W] + ab;
            }
        }
        const pr = mr - rows + 1, pc = mc - cols + 1, n = rows * cols;
        const scores = new Float32Array(pr * pc);
        for (let r = 0; r < pr; r++) {
            for (let c = 0; c < pc; c++) {
                const a = r * W + c, b = a + cols, d = (r + rows) * W + c, e = d + cols;
                if (bad[e] - bad[b] - bad[d] + bad[a] > 0) { scores[r * pc + c] = NaN; continue; }
                const sum = s1[e] - s1[b] - s1[d] + s1[a];
                const v = (s2[e] - s2[b] - s2[d] + s2[a]) - sum * sum / n;
                if (v < n * MIN_STD * MIN_STD * 0.25) { scores[r * pc + c] = NaN; continue; }
                let num = 0;
                for (let i = 0; i < rows; i++) {
                    const mo = (r + i) * mc + c, to = i * cols;
                    for (let j = 0; j < cols; j++) num += t[to + j] * map[mo + j];
                }
                scores[r * pc + c] = num / (tnorm * Math.sqrt(v));
            }
        }
        return { scores: scores, rows: pr, cols: pc };
    }

    // Best place and the best one that isn't part of the same peak
    function peaks(sc) {
        let best = -2, br = -1, bc = -1;
        for (let r = 0; r < sc.rows; r++) {
            for (let c = 0; c < sc.cols; c++) {
                const v = sc.scores[r * sc.cols + c];
                if (v > best) { best = v; br = r; bc = c; }
            }
        }
        let second = -1;
        for (let r = 0; r < sc.rows; r++) {
            for (let c = 0; c < sc.cols; c++) {
                if (Math.abs(r - br) <= PEAK_EXCLUDE && Math.abs(c - bc) <= PEAK_EXCLUDE) continue;
                const v = sc.scores[r * sc.cols + c];
                if (v > second) second = v;
            }
        }
        return { score: best, r: br, c: bc, second: second };
    }

    // Sub-pixel position of a peak from its neighbours (parabola)
    function refine(sc, r, c) {
        const at = function(rr, cc) {
            if (rr < 0 || cc < 0 || rr >= sc.rows || cc >= sc.cols) return NaN;
            return sc.scores[rr * sc.cols + cc];
        };
        const fit = function(a, b, d) {
            const den = a - 2 * b + d;
            if (!(Math.abs(den) > 1e-9)) return 0;
            return Math.max(-0.5, Math.min(0.5, 0.5 * (a - d) / den));
        };
        const v = at(r, c);
        return [r + fit(at(r - 1, c), v, at(r + 1, c)), c + fit(at(r, c - 1), v, at(r, c + 1))];
    }

    /**
     * The template for one height on the climb: the ground around the takeoff spot (homeN, homeE
     * metres from below the Solo), turned to the Solo's heading at the time.
     */
    function reference(pyr, cam, view, homeN, homeE) {
        const gsd = Math.max(0.02, GSD_PER_M * view.height);
        const t = render(pyr, cam, view, homeN, homeE, view.yaw, gsd, ROWS, COLS, new Float32Array(ROWS * COLS));
        let sum = 0;
        for (let k = 0; k < t.length; k++) {
            if (t[k] !== t[k]) return { ok: false, reason: 'the takeoff spot is out of view' };
            sum += t[k];
        }
        const mean = sum / t.length;
        let ss = 0;
        for (let k = 0; k < t.length; k++) {
            t[k] -= mean;
            ss += t[k] * t[k];
        }
        const std = Math.sqrt(ss / t.length);
        if (std < MIN_STD) return { ok: false, reason: 'the ground is too plain to recognise' };
        const tnorm = Math.sqrt(ss);
        // Does the ground right around it look the same (tiles, stripes, even lawn)?
        const R = SELF_RADIUS, mr = ROWS + 2 * R, mc = COLS + 2 * R;
        const around = render(pyr, cam, view, homeN, homeE, view.yaw, gsd, mr, mc, new Float32Array(mr * mc));
        const p = peaks(correlate(around, mr, mc, t, tnorm, ROWS, COLS));
        if (p.second > MAX_SELF) return { ok: false, reason: 'the ground repeats (tiles, stripes or even grass)' };
        return {
            ok: true,
            ref: { height: view.height, yaw: view.yaw, gsd: gsd, rows: ROWS, cols: COLS, t: t, tnorm: tnorm,
                   std: std, self: p.second }
        };
    }

    // Templates to try at this height, best first: the highest one made at or below it (it fits in
    // the view whatever the heading), then the lower ones, then one made a little higher.
    function pickReferences(refs, height) {
        const below = refs.filter(function(r) { return r.height <= height * 1.1; })
            .sort(function(a, b) { return b.height - a.height; });
        const above = refs.filter(function(r) { return r.height > height * 1.1 && r.height <= height * MAX_REF_RATIO; })
            .sort(function(a, b) { return a.height - b.height; });
        return below.concat(above);
    }

    /**
     * Find the takeoff spot in this frame. prior: {n, e (where it should be, m from below the Solo),
     * radius (m to search), scale (last height correction, 1 = the telemetry height)}.
     */
    function match(pyr, cam, view, refs, prior) {
        const list = pickReferences(refs, view.height);
        if (!list.length) return { ok: false, reason: 'too low for the saved pictures' };
        let res = null;
        for (let k = 0; k < list.length && k < 2; k++) {
            res = matchOne(pyr, cam, view, list[k], prior);
            if (res.reason !== 'not in view') break;
        }
        return res;
    }

    function matchOne(pyr, cam, view, ref, prior) {
        const S = Math.max(2, Math.min(MAX_SEARCH_PX, Math.round(prior.radius / ref.gsd)));
        const mr = ref.rows + 2 * S, mc = ref.cols + 2 * S;
        const map = new Float32Array(mr * mc);
        const s0 = Math.max(0.6, Math.min(1.6, prior.scale || 1));
        let best = null;
        [s0, s0 / SCALE_STEP, s0 * SCALE_STEP].forEach(function(s) {
            const v = { height: view.height * s, yaw: view.yaw };
            render(pyr, cam, v, prior.n, prior.e, ref.yaw, ref.gsd, mr, mc, map);
            const sc = correlate(map, mr, mc, ref.t, ref.tnorm, ref.rows, ref.cols);
            const p = peaks(sc);
            if (p.r < 0) return;
            if (!best || p.score > best.score) {
                best = p;
                best.scale = s;
                best.sc = sc;
                best.at = refine(sc, p.r, p.c);
            }
        });
        if (!best) return { ok: false, reason: 'not in view' };
        const out = { ok: false, score: best.score, second: best.second, scale: best.scale, refHeight: ref.height };
        if (best.score < MIN_SCORE) { out.reason = 'no match'; return out; }
        if (best.score - best.second < MIN_MARGIN) { out.reason = 'not sure (another spot looks alike)'; return out; }
        if (best.r === 0 || best.c === 0 || best.r === best.sc.rows - 1 || best.c === best.sc.cols - 1) {
            out.reason = 'at the edge of the search';
            return out;
        }
        // template centre: shift from the map centre, in the template's turned frame -> north / east
        const u = (S - best.at[0]) * ref.gsd, v = (best.at[1] - S) * ref.gsd;
        const r0 = ref.yaw * Math.PI / 180, cr = Math.cos(r0), sr = Math.sin(r0);
        const n = prior.n + u * cr - v * sr, e = prior.e + u * sr + v * cr;
        const H = view.height * best.scale;
        const y = view.yaw * Math.PI / 180, cy = Math.cos(y), sy = Math.sin(y);
        const fwd = n * cy + e * sy, rgt = -n * sy + e * cy;
        const dist = Math.sqrt(fwd * fwd + rgt * rgt + H * H);
        // the template's outline in the live picture, for the overlay
        const hu = (ref.rows / 2) * ref.gsd, hv = (ref.cols / 2) * ref.gsd;
        const lv = { height: H, yaw: view.yaw };
        const outline = [[hu, -hv], [hu, hv], [-hu, hv], [-hu, -hv]].map(function(q) {
            return project(n + q[0] * cr - q[1] * sr, e + q[0] * sr + q[1] * cr, cam, lv);
        });
        out.ok = true;
        out.n = n;
        out.e = e;
        out.angle_x = Math.atan2(rgt, H);
        out.angle_y = Math.atan2(-fwd, H);
        out.distance = dist;
        out.size = 2 * Math.atan(hu / dist);
        out.ahead = fwd;
        out.right = rgt;
        out.below = H;
        out.center = project(n, e, cam, lv);
        out.outline = outline.every(function(p) { return p; }) ? outline : null;
        return out;
    }

    const api = {
        COLS: COLS, ROWS: ROWS, MIN_SCORE: MIN_SCORE, MIN_MARGIN: MIN_MARGIN,
        pyramid: pyramid, render: render, reference: reference, match: match, project: project,
        pickReferences: pickReferences
    };
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
    else root.SpotFinder = api;
})(this);
