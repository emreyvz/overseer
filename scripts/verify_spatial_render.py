"""Offline visual test for the 3D SPATIAL surface: replicates the frontend mesh pipeline
(cleanMask -> fillHoles -> fillDepth -> median+heavy-blur -> ground-damp -> triangulate, no cull)
on a live frame and RENDERS it (painter's algorithm) so the surface can be verified headlessly —
smooth, filled, no black holes. Run with the backend up: uv run python scripts/verify_spatial_render.py"""
import sys, os, json, base64, requests, numpy as np, cv2
TMP = os.path.expandvars(r"%TEMP%")
SKYCULL = 0.015; DAMP = 0.16; ZNEAR, ZFAR, GAMMA = 1.0, 9.0, 1.6

def fetch(sid):
    r = requests.get(f"http://127.0.0.1:8787/api/spatial/{sid}?grid=384", timeout=90).json()
    s = r["scene"]; w, h, fov = s["w"], s["h"], s["fov"]
    rgb = cv2.cvtColor(cv2.imdecode(np.frombuffer(base64.b64decode(s["image"]), np.uint8), cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB)
    disp = np.frombuffer(base64.b64decode(s["depth"]), np.float32).reshape(h, w).copy()
    return rgb, disp, w, h, fov

def clean_mask(disp, w, h):
    base = (disp >= SKYCULL).astype(np.uint8)
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    m = cv2.morphologyEx(base, cv2.MORPH_CLOSE, k)
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, k)
    m = cv2.erode(m, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)), iterations=5)  # trim smear-prone boundary
    # fill holes: flood bg from border, unreached 0 -> 1
    ff = m.copy(); mask2 = np.zeros((h + 2, w + 2), np.uint8)
    inv = (m == 0).astype(np.uint8) * 255
    cv2.floodFill(inv, mask2, (0, 0), 0)   # from border; remaining 255 = enclosed holes
    holes = inv > 0
    filled = m.copy(); filled[holes] = 1
    return base, filled, int(holes.sum())

def fill_depth(disp, keep):
    out = disp.copy()
    need = (keep == 1) & (out < SKYCULL)
    for _ in range(80):
        if not need.any(): break
        valid = (out >= SKYCULL).astype(np.float32)
        vals = out * valid
        s = (np.roll(vals,1,0)+np.roll(vals,-1,0)+np.roll(vals,1,1)+np.roll(vals,-1,1))
        c = (np.roll(valid,1,0)+np.roll(valid,-1,0)+np.roll(valid,1,1)+np.roll(valid,-1,1))
        upd = need & (c > 0)
        out[upd] = s[upd] / c[upd]
        need = (keep == 1) & (out < SKYCULL)
    return out

def smooth(disp):
    d = cv2.medianBlur(disp.astype(np.float32), 3)
    for _ in range(4): d = cv2.blur(d, (13, 13))
    return d

def zof(d): return ZNEAR + np.power(1 - d, GAMMA) * (ZFAR - ZNEAR)

def build_and_render(name, sid):
    rgb, disp, w, h, fov = fetch(sid)
    base, keep, nholes = clean_mask(disp, w, h)
    fgd = smooth(fill_depth(disp, keep))
    fx = 0.5 * w / np.tan(np.radians(fov) / 2); cx, cy = w / 2, h / 2
    xs, ys = np.meshgrid(np.arange(w), np.arange(h))
    Z = zof(fgd)
    X = (xs - cx) * Z / fx; Y = -(ys - cy) * Z / fx
    # flatten: quadratic Y=a+bZ+cZ^2 over lower-half kept
    sel = (keep == 1) & (ys >= h * 0.5)
    if sel.sum() > 50:
        zz = Z[sel].ravel(); yy = Y[sel].ravel()
        A = np.vstack([np.ones_like(zz), zz, zz * zz]).T
        coef, *_ = np.linalg.lstsq(A, yy, rcond=None)
        Y = Y - (coef[0] + coef[1] * Z + coef[2] * Z * Z)
    Y = Y * DAMP   # damp height toward the ground plane -> smooth, no smears
    V = np.stack([X, Y, -Z], -1)  # (h,w,3)
    # triangles: every quad fully kept (NO maxlen)
    tris = []
    kp = keep
    for y in range(h - 1):
        for x in range(w - 1):
            tl, tr, bl, br = (y, x), (y, x + 1), (y + 1, x), (y + 1, x + 1)
            if kp[tl] and kp[bl] and kp[tr]: tris.append((tl, bl, tr))
            if kp[tr] and kp[bl] and kp[br]: tris.append((tr, bl, br))
    # render from a 3/4 above angle (painter's algorithm)
    yaw, pitch = np.radians(22), np.radians(26)
    Ry = np.array([[np.cos(yaw),0,np.sin(yaw)],[0,1,0],[-np.sin(yaw),0,np.cos(yaw)]])
    Rx = np.array([[1,0,0],[0,np.cos(pitch),-np.sin(pitch)],[0,np.sin(pitch),np.cos(pitch)]])
    R = Rx @ Ry
    Vf = V.reshape(-1, 3) @ R.T
    IW, IH = 1200, 700
    px, py, pz = Vf[:, 0], Vf[:, 1], Vf[:, 2]
    valid = (kp.ravel() == 1)
    minx, maxx = px[valid].min(), px[valid].max(); miny, maxy = py[valid].min(), py[valid].max()
    sc = 0.86 * min(IW / (maxx - minx + 1e-6), IH / (maxy - miny + 1e-6))
    ox, oy = IW / 2 - sc * (minx + maxx) / 2, IH / 2 + sc * (miny + maxy) / 2
    def proj(iy, ix):
        i = iy * w + ix; return (int(ox + sc * px[i]), int(oy - sc * py[i]))
    img = np.zeros((IH, IW, 3), np.uint8)
    # sort triangles far->near by mean camera-space depth (pz smaller = farther)
    def tdepth(t): return (pz[t[0][0]*w+t[0][1]] + pz[t[1][0]*w+t[1][1]] + pz[t[2][0]*w+t[2][1]]) / 3
    tris.sort(key=tdepth)
    for t in tris:
        pts = np.array([proj(*t[0]), proj(*t[1]), proj(*t[2])], np.int32)
        col = rgb[t[0]].astype(int) + rgb[t[1]].astype(int) + rgb[t[2]].astype(int)
        c = (int(col[2]//3), int(col[1]//3), int(col[0]//3))  # BGR for cv2
        cv2.fillConvexPoly(img, pts, c)
    # black-hole metric: within the rendered silhouette bbox, % black pixels
    gray = img.max(2)
    ys2, xs2 = np.where(gray > 4)
    holepct = -1
    if len(xs2):
        x0,x1,y0,y1 = xs2.min(),xs2.max(),ys2.min(),ys2.max()
        roi = gray[y0:y1+1, x0:x1+1]
        # black inside the convex hull of the silhouette
        hull = np.zeros_like(gray); pts = np.column_stack([xs2, ys2]); hh = cv2.convexHull(pts)
        cv2.fillConvexPoly(hull, hh, 1); insideb = (gray <= 4) & (hull == 1)
        holepct = 100.0 * insideb.sum() / max(1, (hull == 1).sum())
    out = f"{TMP}/rv_{name}.png"; cv2.imwrite(out, img)
    print(f"{name}: mask-holes-filled={nholes}px  tris={len(tris)}  render-black-inside={holepct:.2f}%  -> {out}")

for nm, sid in [("airport", "8"), ("street", "6"), ("beach", "18")]:
    try: build_and_render(nm, sid)
    except Exception as e: print(nm, "FAIL", repr(e))
