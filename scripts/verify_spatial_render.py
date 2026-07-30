"""Offline visual test for the 3D SPATIAL surface. Replicates the frontend mesh pipeline
(cleanMask -> fillHoles -> fillDepth -> median + LIGHT blur -> de-bow ground (keep RELIEF) ->
triangulate with a depth-discontinuity cull) on a live frame, renders a background fill layer +
the foreground on top (painter's algorithm) from a LOW angle, and reports whether objects stand up.
Verifies headlessly, without a GPU, that the ground is flat/filled AND objects rise as 3D volumes.
Run with the backend up:  uv run python scripts/verify_spatial_render.py"""
import os, base64, requests, numpy as np, cv2

TMP = os.path.expandvars(r"%TEMP%")
SKYCULL = 0.015; RELIEF = 1.0; DISP_JUMP = 0.055; ZNEAR, ZFAR, GAMMA = 1.0, 9.0, 1.6


def fetch(sid):
    r = requests.get(f"http://127.0.0.1:8787/api/spatial/{sid}?grid=384", timeout=90).json()
    s = r["scene"]; w, h, fov = s["w"], s["h"], s["fov"]

    def layer(imk, dpk):
        if imk not in s or dpk not in s or not s[imk]:
            return None, None
        rgb = cv2.cvtColor(cv2.imdecode(np.frombuffer(base64.b64decode(s[imk]), np.uint8), cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB)
        disp = np.frombuffer(base64.b64decode(s[dpk]), np.float32).reshape(h, w).copy()
        return rgb, disp

    fg_rgb, fg_disp = layer("image", "depth")
    bg_rgb, bg_disp = layer("bg_image", "bg_depth")
    return fg_rgb, fg_disp, bg_rgb, bg_disp, w, h, fov


def clean_mask(disp, w, h):
    base = (disp >= SKYCULL).astype(np.uint8)
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    m = cv2.morphologyEx(base, cv2.MORPH_CLOSE, k)
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, k)
    m = cv2.erode(m, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)), iterations=5)
    ff = np.zeros((h + 2, w + 2), np.uint8)
    inv = (m == 0).astype(np.uint8) * 255
    cv2.floodFill(inv, ff, (0, 0), 0)          # flood bg from border; remaining 255 = enclosed holes
    filled = m.copy(); filled[inv > 0] = 1
    return filled, int((inv > 0).sum())


def fill_depth(disp, keep):
    out = disp.copy()
    need = (keep == 1) & (out < SKYCULL)
    for _ in range(80):
        if not need.any(): break
        valid = (out >= SKYCULL).astype(np.float32); vals = out * valid
        s = np.roll(vals, 1, 0) + np.roll(vals, -1, 0) + np.roll(vals, 1, 1) + np.roll(vals, -1, 1)
        c = np.roll(valid, 1, 0) + np.roll(valid, -1, 0) + np.roll(valid, 1, 1) + np.roll(valid, -1, 1)
        upd = need & (c > 0); out[upd] = s[upd] / c[upd]
        need = (keep == 1) & (out < SKYCULL)
    return out


def smooth(disp):                                # LIGHT: median despeckle + one gentle blur pass
    d = cv2.medianBlur(disp.astype(np.float32), 3)
    return cv2.blur(d, (5, 5))


def zof(d): return ZNEAR + np.power(1 - d, GAMMA) * (ZFAR - ZNEAR)


def build_layer(rgb, disp, keep, cull, zbias, coef, w, h, fx, cx, cy, base_v=0):
    """Return (verts (N,3), cols (N,3 uint8), tris list of (i,j,k) into verts) for one layer."""
    Z = zof(disp)
    xs, ys = np.meshgrid(np.arange(w), np.arange(h))
    X = (xs - cx) * Z / fx
    Y = -(ys - cy) * Z / fx
    if coef is not None:                         # de-bow: subtract trend, KEEP residual * RELIEF
        Y = (Y - (coef[0] + coef[1] * Z + coef[2] * Z * Z)) * RELIEF
    V = np.stack([X, Y, -(Z + zbias)], -1).reshape(-1, 3)
    cols = rgb.reshape(-1, 3)
    kp = keep.ravel(); dr = disp.ravel()
    tris = []
    for y in range(h - 1):
        row = y * w
        for x in range(w - 1):
            tl = row + x; tr = tl + 1; bl = tl + w; br = bl + 1
            for a, b, c in ((tl, bl, tr), (tr, bl, br)):
                if not (kp[a] and kp[b] and kp[c]): continue
                if cull:
                    da, db, dc = dr[a], dr[b], dr[c]
                    if max(abs(da - db), abs(db - dc), abs(da - dc)) > DISP_JUMP: continue  # silhouette skirt
                tris.append((a + base_v, b + base_v, c + base_v))
    return V, cols, tris


def build_and_render(name, sid):
    fg_rgb, fg_disp, bg_rgb, bg_disp, w, h, fov = fetch(sid)
    fx = 0.5 * w / np.tan(np.radians(fov) / 2); cx, cy = w / 2, h / 2
    keep, nholes = clean_mask(fg_disp, w, h)
    fgd = smooth(fill_depth(fg_disp, keep))

    # shared ground trend from fg lower-half kept pixels (Y = a + bZ + cZ^2)
    Z = zof(fgd); xs, ys = np.meshgrid(np.arange(w), np.arange(h))
    Yraw = -(ys - cy) * Z / fx
    sel = (keep == 1) & (ys >= h * 0.5)
    coef = None
    if sel.sum() > 50:
        zz = Z[sel].ravel(); yy = Yraw[sel].ravel()
        A = np.vstack([np.ones_like(zz), zz, zz * zz]).T
        coef, *_ = np.linalg.lstsq(A, yy, rcond=None)

    # relief stats: residual height above the fitted ground for kept pixels
    resid = (Yraw - (coef[0] + coef[1] * Z + coef[2] * Z * Z)) * RELIEF if coef is not None else Yraw
    rk = resid[keep == 1]
    standing = float((rk > 0.2).sum()) * 100.0 / max(1, rk.size)
    p95 = float(np.percentile(rk, 95)) if rk.size else 0.0

    # background fill layer first (no cull), foreground on top (depth-jump cull)
    layers = []
    base = 0
    if bg_rgb is not None:
        bgk = (bg_disp >= SKYCULL).astype(np.uint8)
        bgd = smooth(bg_disp)
        Vb, Cb, Tb = build_layer(bg_rgb, bgd, bgk, False, 0.04, coef, w, h, fx, cx, cy, base)
        layers.append((Vb, Cb, Tb)); base += Vb.shape[0]
    Vf, Cf, Tf = build_layer(fg_rgb, fgd, keep, True, 0.0, coef, w, h, fx, cx, cy, base)
    layers.append((Vf, Cf, Tf))
    ntris_fg = len(Tf)

    V = np.concatenate([L[0] for L in layers], 0)
    C = np.concatenate([L[1] for L in layers], 0)
    tris = [t for L in layers for t in L[2]]

    # render from a LOW angle so standing objects read as vertical relief
    yaw, pitch = np.radians(24), np.radians(15)
    Ry = np.array([[np.cos(yaw), 0, np.sin(yaw)], [0, 1, 0], [-np.sin(yaw), 0, np.cos(yaw)]])
    Rx = np.array([[1, 0, 0], [0, np.cos(pitch), -np.sin(pitch)], [0, np.sin(pitch), np.cos(pitch)]])
    Vf2 = V @ (Rx @ Ry).T
    IW, IH = 1200, 760
    px, py, pz = Vf2[:, 0], Vf2[:, 1], Vf2[:, 2]
    # frame to the foreground footprint
    fgmask = np.zeros(V.shape[0], bool); fgmask[base:] = (keep.ravel() == 1)
    minx, maxx = px[fgmask].min(), px[fgmask].max(); miny, maxy = py[fgmask].min(), py[fgmask].max()
    sc = 0.82 * min(IW / (maxx - minx + 1e-6), IH / (maxy - miny + 1e-6))
    ox, oy = IW / 2 - sc * (minx + maxx) / 2, IH / 2 + sc * (miny + maxy) / 2
    sx = (ox + sc * px).astype(np.int32); sy = (oy - sc * py).astype(np.int32)

    img = np.zeros((IH, IW, 3), np.uint8)
    order = sorted(range(len(tris)), key=lambda t: (pz[tris[t][0]] + pz[tris[t][1]] + pz[tris[t][2]]))
    for ti in order:
        a, b, c = tris[ti]
        pts = np.array([[sx[a], sy[a]], [sx[b], sy[b]], [sx[c], sy[c]]], np.int32)
        col = (C[a].astype(int) + C[b].astype(int) + C[c].astype(int)) // 3
        cv2.fillConvexPoly(img, pts, (int(col[2]), int(col[1]), int(col[0])))

    out = f"{TMP}/rv_{name}.png"; cv2.imwrite(out, img)
    print(f"{name}: holes-filled={nholes}px  fg-tris={ntris_fg}  standing(resid>0.2)={standing:.1f}%  "
          f"relief-P95={p95:.2f}u  bg={'yes' if bg_rgb is not None else 'no'}  -> {out}")


for nm, sid in [("airport", "8"), ("street", "6"), ("beach", "18")]:
    try: build_and_render(nm, sid)
    except Exception as e: print(nm, "FAIL", repr(e))
