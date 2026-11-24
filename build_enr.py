#!/usr/bin/env python3
"""สร้างแผ่นภาพ (XYZ tiles) จาก Enroute Chart (ICAO) ของ AIP ประเทศไทย (PDF เวกเตอร์ ระบบฉาย Lambert Conformal Conic)

ขั้นตอน
1. อ่านเส้นพิกัด (graticule) สีเทาในไฟล์ PDF โดยตรง → จุดตัดเส้นเมริเดียน/เส้นขนานทุก 1° = จุดอ้างอิง (GCP)
2. ตรวจคุณภาพจุดอ้างอิง (จำนวน + ความคลาดเคลื่อนเมื่อเทียบกับสมการพหุนาม) ถ้าไม่ผ่าน → หยุด ไม่เผยแพร่
3. แปลง PDF เป็นภาพ พื้นขาว = โปร่งใส → gdalwarp (TPS) เป็น EPSG:3857 → gdal2tiles (--xyz)
4. เขียน meta.json (AIRAC, วันที่มีผล, ซูม, กรอบ) ให้แอปอ่าน

ใช้: python build_enr.py --pdf enr/ENR.pdf --out site [--dpi 300] [--zmin 5] [--zmax 11]
ต้องมี: pymupdf, numpy, pillow, gdal-bin (gdal_translate, gdalwarp) และ gdal2tiles.py
"""
import argparse, json, math, os, re, shutil, subprocess, sys, datetime
import numpy as np
import pymupdf
from PIL import Image

Image.MAX_IMAGE_PIXELS = None
GREY = 0.596  # สีเส้นพิกัดในชาร์ต (RGB 0.596 เท่ากันทุกช่อง)


def die(msg):
    print("::error::" + msg); sys.exit(1)


def labels(pg):
    """ป้ายองศาที่ขอบชาร์ต: 98°E (บน/ล่าง) และ 14°N (ซ้าย/ขวา)"""
    E, N = [], []
    for w in pg.get_text("words"):
        m = re.fullmatch(r"(\d{1,3})°([NE])", w[4])
        if not m: continue
        cx, cy = (w[0] + w[2]) / 2, (w[1] + w[3]) / 2
        (E if m.group(2) == "E" else N).append((int(m.group(1)), cx, cy))
    if len(E) < 4 or len(N) < 4: die(f"หาป้ายองศาที่ขอบชาร์ตไม่พอ (E={len(E)}, N={len(N)}) — รูปแบบชาร์ตอาจเปลี่ยน")
    return E, N


def approx_fn(E, N):
    """สมการคร่าวๆ px → (lon, lat) จากป้ายขอบ ใช้แค่ระบุว่าจุดตัดเป็นองศาไหน"""
    ys = sorted(set(round(e[2]) for e in E)); top, bot = min(ys), max(ys)
    def fit(rows):  # lon = a + b*x  จากป้ายแถวเดียว
        x = np.array([r[1] for r in rows]); v = np.array([r[0] for r in rows]); b, a = np.polyfit(x, v, 1); return a, b
    T = fit([e for e in E if abs(e[2] - top) < 5]); B = fit([e for e in E if abs(e[2] - bot) < 5])
    yv = np.array([n[2] for n in N]); vv = np.array([n[0] for n in N]); c1, c0 = np.polyfit(yv, vv, 1)
    def f(x, y):
        t = min(1, max(0, (y - top) / (bot - top)))
        lon = (T[0] + T[1] * x) * (1 - t) + (B[0] + B[1] * x) * t
        return lon, c0 + c1 * y
    return f


def gcps(pg, f):
    segs = []
    for d in pg.get_drawings():
        c = d.get("color")
        if not c or any(abs(v - GREY) > 0.01 for v in c): continue
        for it in d["items"]:
            if it[0] == "l":
                p, q = it[1], it[2]; L = math.hypot(q.x - p.x, q.y - p.y)
                if L > 30: segs.append((p.x, p.y, q.x, q.y))
    V = [s for s in segs if abs(s[2] - s[0]) < 0.2 * abs(s[3] - s[1])]
    H = [s for s in segs if abs(s[3] - s[1]) < 0.2 * abs(s[2] - s[0])]
    M, P = {}, {}
    for s in V:
        lo, _ = f((s[0] + s[2]) / 2, (s[1] + s[3]) / 2); k = round(lo)
        if abs(lo - k) < 0.25: M.setdefault(k, []).append(s)
    for s in H:
        _, la = f((s[0] + s[2]) / 2, (s[1] + s[3]) / 2); k = round(la)
        if abs(la - k) < 0.25: P.setdefault(k, []).append(s)
    def poly(ss, i):
        pts = sorted({(round(s[0], 2), round(s[1], 2)) for s in ss} | {(round(s[2], 2), round(s[3], 2)) for s in ss}, key=lambda p: p[i])
        return np.array(pts)
    out = []
    for la, ss in P.items():
        PP = poly(ss, 0)
        for lo, ms in M.items():
            MM = poly(ms, 1); y = PP[:, 1].mean(); ok = True
            for _ in range(6):
                if y < MM[0, 1] - 1 or y > MM[-1, 1] + 1: ok = False; break
                x = float(np.interp(y, MM[:, 1], MM[:, 0]))
                if x < PP[0, 0] - 1 or x > PP[-1, 0] + 1: ok = False; break
                y = float(np.interp(x, PP[:, 0], PP[:, 1]))
            if ok: out.append((x, y, lo, la))
    return out


def check(g):
    """ตรวจจุดอ้างอิงด้วยพหุนามกำลัง 3: ระบบฉาย LCC ของชาร์ตทั้งแผ่นต้องเข้าได้ดีมาก ถ้ามีจุดผิด (ระบุองศาผิด) จะคลาดเป็นองศา"""
    A = np.array([[1, x, y, x*x, x*y, y*y, x**3, x*x*y, x*y*y, y**3] for x, y, _, _ in [(gx / 1000, gy / 1000, 0, 0) for gx, gy, _, _ in g]])
    worst = 0
    for col in (2, 3):
        v = np.array([p[col] for p in g]); c, *_ = np.linalg.lstsq(A, v, rcond=None); worst = max(worst, float(np.abs(A @ c - v).max()))
    return worst


def meta_text(pg):
    t = pg.get_text()
    am = re.search(r"AIRAC\s+AIP\s+AMENDMENT\s+(\d{1,2}/\d{2})", t)
    ef = re.search(r"EFFECTIVE\s+DATE\s+(\d{1,2}\s+[A-Z]{3}\s+\d{2})", t)
    return (am.group(1) if am else ""), (ef.group(1) if ef else "")


def run(cmd):
    print("+", " ".join(cmd[:6]), "..." if len(cmd) > 6 else ""); subprocess.run(cmd, check=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", required=True); ap.add_argument("--out", default="site")
    ap.add_argument("--dpi", type=int, default=300); ap.add_argument("--zmin", type=int, default=5); ap.add_argument("--zmax", type=int, default=11)
    ap.add_argument("--work", default="work"); ap.add_argument("--gdal2tiles", default="gdal2tiles.py")
    a = ap.parse_args()
    os.makedirs(a.work, exist_ok=True)
    doc = pymupdf.open(a.pdf); pg = doc[0]
    E, N = labels(pg); f = approx_fn(E, N); g = gcps(pg, f)
    lons = sorted({p[2] for p in g}); lats = sorted({p[3] for p in g})
    print(f"จุดอ้างอิง {len(g)} จุด · ลองจิจูด {lons} · ละติจูด {lats}")
    if len(g) < 80: die(f"จุดอ้างอิงน้อยเกินไป ({len(g)}) — รูปแบบเส้นพิกัดในชาร์ตอาจเปลี่ยน ไม่เผยแพร่")
    if len({(p[2], p[3]) for p in g}) != len(g): die("พบจุดอ้างอิงซ้ำองศาเดียวกัน — ไม่เผยแพร่")
    res = check(g); print(f"ความคลาดเคลื่อนสูงสุดเทียบพหุนาม {res:.4f}°")
    if res > 0.02: die(f"จุดอ้างอิงไม่สอดคล้องกัน (คลาด {res:.3f}°) — ไม่เผยแพร่")
    airac, eff = meta_text(pg); print("AIRAC", airac, "· มีผล", eff)
    if not airac or not eff: die("อ่าน AIRAC / วันที่มีผลจากชาร์ตไม่ได้ — ไม่เผยแพร่")
    z = a.dpi / 72
    pix = pg.get_pixmap(matrix=pymupdf.Matrix(z, z), alpha=False)
    im = Image.frombytes("RGB", (pix.width, pix.height), pix.samples); del pix
    arr = np.asarray(im); alpha = np.where(arr.min(axis=2) > 236, 0, 255).astype(np.uint8)
    Image.fromarray(np.dstack([arr, alpha]), "RGBA").save(os.path.join(a.work, "chart.png")); del arr, alpha, im
    gc = []
    for x, y, lo, la in g: gc += ["-gcp", f"{x*z:.2f}", f"{y*z:.2f}", str(lo), str(la)]
    run(["gdal_translate", "-q", "-of", "GTiff", "-a_srs", "EPSG:4326", *gc, os.path.join(a.work, "chart.png"), os.path.join(a.work, "gcp.tif")])
    w0, w1, s0, s1 = min(lons) - 0.6, max(lons) + 0.7, min(lats) - 0.6, max(lats) + 0.7
    run(["gdalwarp", "-q", "-overwrite", "-tps", "-r", "bilinear", "-t_srs", "EPSG:3857", "-te_srs", "EPSG:4326", "-te", str(w0), str(s0), str(w1), str(s1),
         "-co", "COMPRESS=DEFLATE", "-co", "BIGTIFF=IF_SAFER", os.path.join(a.work, "gcp.tif"), os.path.join(a.work, "chart_3857.tif")])
    tdir = os.path.join(a.out, "tiles")
    if os.path.isdir(tdir): shutil.rmtree(tdir)
    run([*a.gdal2tiles.split(), "-q", "--xyz", "-z", f"{a.zmin}-{a.zmax}", "-w", "none", "-r", "bilinear", "--processes=4", os.path.join(a.work, "chart_3857.tif"), tdir])
    n = sum(len(fs) for _, _, fs in os.walk(tdir))
    meta = {"airac": airac, "effective": eff, "source": os.path.basename(a.pdf), "zmin": a.zmin, "zmax": a.zmax, "bounds": [w0, s0, w1, s1],
            "gcps": len(g), "residual_deg": round(res, 5), "tiles": n, "built": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%MZ")}
    json.dump(meta, open(os.path.join(a.out, "meta.json"), "w"), ensure_ascii=False, indent=1)
    print(f"เสร็จ: {n} แผ่น · AIRAC {airac} · มีผล {eff}")


if __name__ == "__main__":
    main()
