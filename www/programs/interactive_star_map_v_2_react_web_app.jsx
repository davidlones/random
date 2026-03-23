import React, { useEffect, useMemo, useRef, useState } from "react";

/**
 * Interactive Star Map v2 — single-file React app
 * - Loads StarFinder-style text or dat.json
 * - Equatorial ↔ Galactic, RA rotation, pan/zoom, HiDPI
 * - Stars/DSOs rendered as point cores + soft pulsing halos
 * - Colors: stars by spectral class (approx. blackbody); DSOs by redshift/type
 * - Click to inspect: details panel using all parsed fields (mag, spectral, z, notes)
 * - Minimal UI with Tailwind classes
 */

// ---------------- Math, Conversions ----------------
const DEG = Math.PI / 180;
const RAD = 180 / Math.PI;

function hmsToDeg(hms) {
  const m = String(hms).trim().match(/^(\d{1,2}):(\d{2}):(\d{2}(?:\.\d+)?)/);
  if (!m) return null;
  const [_, h, mi, s] = m;
  return 15 * (parseInt(h) + parseInt(mi) / 60 + parseFloat(s) / 3600);
}
function dmsToDeg(dms) {
  const m = String(dms).trim().match(/^([+\-]?\d{1,3}):(\d{2}):(\d{2}(?:\.\d+)?)/);
  if (!m) return null;
  const sign = m[1].startsWith("-") ? -1 : 1;
  const d = Math.abs(parseFloat(m[1]));
  const mi = parseInt(m[2]);
  const s = parseFloat(m[3]);
  return sign * (d + mi / 60 + s / 3600);
}
function degToHMS(ra) {
  const total = (ra / 15 + 24) % 24; // hours
  const h = Math.floor(total);
  const m = Math.floor((total - h) * 60);
  const s = ((total - h) * 60 - m) * 60;
  return `${h.toString().padStart(2, "0")}:${m.toString().padStart(2, "0")}:${s.toFixed(1).padStart(4, "0")}`;
}
function degToDMS(deg) {
  const sign = deg < 0 ? "-" : "+";
  const a = Math.abs(deg);
  const d = Math.floor(a);
  const m = Math.floor((a - d) * 60);
  const s = ((a - d) * 60 - m) * 60;
  return `${sign}${d.toString().padStart(2, "0")}:${m.toString().padStart(2, "0")}:${s.toFixed(0).padStart(2, "0")}`;
}

// Galactic transform (J2000)
const RA_GP = 192.85948 * DEG; // RA of galactic north pole
const DEC_GP = 27.12825 * DEG; // Dec of galactic north pole
const L_CP = 32.93192 * DEG; // Galactic longitude of NCP (ascending node)
function radecToGal(raDeg, decDeg) {
  const ra = raDeg * DEG, dec = decDeg * DEG;
  const sinb = Math.sin(dec) * Math.sin(DEC_GP) + Math.cos(dec) * Math.cos(DEC_GP) * Math.cos(ra - RA_GP);
  const b = Math.asin(Math.max(-1, Math.min(1, sinb)));
  const y = Math.sin(ra - RA_GP) * Math.cos(dec);
  const x = Math.cos(dec) * Math.sin(DEC_GP) * Math.cos(ra - RA_GP) - Math.sin(dec) * Math.cos(DEC_GP);
  let l = Math.atan2(y, x) + L_CP;
  return [(l * RAD + 360) % 360, b * RAD];
}

function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }
function magToCore(m) { return Math.max(0.35, 3.0 - 0.45 * (m ?? 6.5)); } // smaller, point-like

// Kelvin to RGB (approx)
function kelvinToRGB(kelvin) {
  const t = kelvin / 100;
  let r, g, b;
  if (t <= 66) { r = 255; g = clamp(99.4708025861 * Math.log(t) - 161.1195681661, 0, 255); }
  else { r = clamp(329.698727446 * Math.pow(t - 60, -0.1332047592), 0, 255); g = clamp(288.1221695283 * Math.pow(t - 60, -0.0755148492), 0, 255); }
  if (t >= 66) b = 255; else if (t <= 19) b = 0; else b = clamp(138.5177312231 * Math.log(t - 10) - 305.0447927307, 0, 255);
  return [Math.round(r), Math.round(g), Math.round(b)];
}
function spectralToColor(spec) {
  if (!spec) return "#ffffff";
  const letter = spec[0].toUpperCase();
  const subclass = parseFloat(spec.slice(1)) || 5;
  const tempMap = { O: 40000, B: 20000, A: 9000, F: 7000, G: 5800, K: 4500, M: 3200, L: 2000, T: 1200, Y: 800 };
  const T0 = tempMap[letter] || 5800;
  const T = T0 - (subclass / 9) * (T0 * 0.35);
  const [r, g, b] = kelvinToRGB(T);
  return `rgb(${r},${g},${b})`;
}
function dsoColor(o) {
  if (o.z) {
    const z = Math.min(0.1, Math.max(0, o.z));
    const r = 255, g = Math.round(255 * (1 - z * 5)), b = Math.round(255 * (1 - z * 8));
    return `rgba(${r},${g},${b},0.95)`;
  }
  const lut = {
    Galaxy: "rgba(255,220,220,0.95)", Globular: "rgba(255,240,200,0.95)", Open: "rgba(220,235,255,0.95)",
    Nebula: "rgba(200,255,235,0.95)", Planetary: "rgba(200,245,255,0.95)", Double: "rgba(240,240,255,0.95)", Other: "rgba(235,235,235,0.95)"
  };
  return lut[o.kind] || lut.Other;
}

// ---------------- Parsing ----------------
function parseFromRawText(raw) {
  const stars = [], dsos = [];
  const starRe = /^(\s*\d{1,6})\s+(\d{2}:\d{2}:\d{2}(?:\.\d+)?)\s+([+\-]?\d{2}:\d{2}:\d{2})\s+(.+)$/gm;
  let m;
  while ((m = starRe.exec(raw))) {
    const id = parseInt(m[1]);
    const ra = hmsToDeg(m[2]);
    const dec = dmsToDeg(m[3]);
    if (ra == null || dec == null) continue;
    const rest = m[4];
    const magTok = (rest.replace(/\|/g, " ").match(/[\-+]?\d+\.\d+/g) || []).map(Number).find(v => v >= -2.5 && v <= 15.5);
    const specMatch = rest.match(/\b([OBAFGKMLTY])\s*([0-9](?:\.[0-9])?)\s*(I{1,3}|IV|V)?\b/i) || rest.match(/\b([OBAFGKMLTY])\b/i);
    const spectral = specMatch ? (specMatch[1].toUpperCase() + (specMatch[2] ?? "") + (specMatch[3] ?? "")) : null;
    if (magTok == null && !spectral) continue;
    stars.push({ id, ra, dec, mag: magTok ?? 6.5, spectral, raw: m[0] });
  }
  const ngcRe = /^(?:\s*NGC\s+(\d+)\s+)(\d{2}:\d{2}:\d{2}(?:\.\d+)?)\s+([+\-]?\d{2}:\d{2}:\d{2})\s+(-{2,}|[\d.]+)\s+(-{2,}|[\d.]+)\s+([A-Z0-9~])\s+(.+?)\s*$/gm;
  const typeMap = { X: "Galaxy", G: "Globular", O: "Open", N: "Nebula", P: "Planetary", "2": "Double", "1": "Single" };
  let g;
  while ((g = ngcRe.exec(raw))) {
    const ra = hmsToDeg(g[2]), dec = dmsToDeg(g[3]);
    if (ra == null || dec == null) continue;
    const typ = typeMap[g[6]] || "Other";
    const mag = g[4].includes("-") ? null : parseFloat(g[4]);
    const desc = g[7] || "";
    let z = null; // parse z or cz
    const zm = desc.match(/\bz\s*=\s*([0-9]*\.?[0-9]+)/i); if (zm) z = parseFloat(zm[1]);
    const czm = desc.match(/\bcz\s*=\s*([0-9]+)\s*km\/?s/i); if (!z && czm) z = parseFloat(czm[1]) / 299792.458;
    dsos.push({ name: `NGC ${g[1]}`, ra, dec, kind: typ, mag, z, desc, raw: g[0] });
  }
  return { stars, dsos };
}
function parseFromDatJson(obj) {
  if (!obj || typeof obj !== 'object') return { stars: [], dsos: [] };
  const chunks = [];
  for (const [k, v] of Object.entries(obj)) if (typeof v === 'string') chunks.push(v);
  const raw = chunks.join('\n'); // proper newline join
  return parseFromRawText(raw);
}

// ---------------- Rendering helpers ----------------
const MARKERS = {
  Galaxy: (ctx, x, y, s) => { ctx.beginPath(); ctx.arc(x, y, s*0.9, 0, Math.PI*2); ctx.fill(); },
  Globular: (ctx, x, y, s) => { ctx.beginPath(); ctx.arc(x, y, s*0.7, 0, Math.PI*2); ctx.fill(); },
  Open: (ctx, x, y, s) => { ctx.beginPath(); ctx.arc(x, y, s*0.6, 0, Math.PI*2); ctx.fill(); },
  Nebula: (ctx, x, y, s) => { ctx.beginPath(); ctx.arc(x, y, s*0.8, 0, Math.PI*2); ctx.fill(); },
  Planetary: (ctx, x, y, s) => { ctx.beginPath(); ctx.arc(x, y, s*0.6, 0, Math.PI*2); ctx.fill(); },
  Double: (ctx, x, y, s) => { ctx.beginPath(); ctx.arc(x-s*0.6, y, s*0.35, 0, Math.PI*2); ctx.arc(x+s*0.6, y, s*0.35, 0, Math.PI*2); ctx.fill(); },
  Other: (ctx, x, y, s) => { ctx.beginPath(); ctx.arc(x, y, s*0.6, 0, Math.PI*2); ctx.fill(); },
};

// ---------------- Main Canvas ----------------
function StarCanvas({ data, coord = "equatorial" }) {
  const canvasRef = useRef(null);
  const [originRA, setOriginRA] = useState(0);
  const [animate, setAnimate] = useState(true);
  const [layers, setLayers] = useState({ Stars: true, Galaxy: true, Globular: true, Open: true, Nebula: true, Planetary: true, Double: true });
  const [transform, setTransform] = useState({ scale: 1, tx: 0, ty: 0 });
  const [useSpectralColors, setUseSpectralColors] = useState(true);
  const [fuzz, setFuzz] = useState(0.6);
  const [maxCore, setMaxCore] = useState(4.0);
  const [hover, setHover] = useState(null);
  const [picked, setPicked] = useState(null);

  // Derived arrays (transform to galactic if needed)
  const stars = useMemo(() => {
    if (!data?.stars?.length) return [];
    return data.stars.map((s) => {
      if (coord === "galactic") {
        const [l, b] = radecToGal(s.ra, s.dec);
        return { ...s, ra: l, dec: b };
      }
      return s;
    });
  }, [data, coord]);
  const dsos = useMemo(() => {
    if (!data?.dsos?.length) return [];
    return data.dsos.map((o) => {
      if (coord === "galactic") {
        const [l, b] = radecToGal(o.ra, o.dec);
        return { ...o, ra: l, dec: b };
      }
      return o;
    });
  }, [data, coord]);

  // HiDPI setup
  useEffect(() => {
    const c = canvasRef.current;
    if (!c) return;
    const dpr = window.devicePixelRatio || 1;
    const rect = c.getBoundingClientRect();
    c.width = rect.width * dpr;
    c.height = rect.height * dpr;
    const ctx = c.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }, []);

  // Interaction: pan/zoom & clicks
  useEffect(() => {
    const c = canvasRef.current;
    if (!c) return;
    let dragging = false,
      lx = 0,
      ly = 0;
    const onDown = (e) => {
      dragging = true;
      lx = e.clientX;
      ly = e.clientY;
    };
    const onMove = (e) => {
      if (!dragging) return;
      const dx = e.clientX - lx,
        dy = e.clientY - ly;
      lx = e.clientX;
      ly = e.clientY;
      setTransform((t) => ({ ...t, tx: t.tx + dx, ty: t.ty + dy }));
    };
    const onUp = () => (dragging = false);
    const onWheel = (e) => {
      e.preventDefault();
      const f = e.deltaY > 0 ? 0.9 : 1.1;
      setTransform((t) => ({ ...t, scale: clamp(t.scale * f, 0.5, 6) }));
    };
    c.addEventListener("mousedown", onDown);
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    c.addEventListener("wheel", onWheel, { passive: false });
    return () => {
      c.removeEventListener("mousedown", onDown);
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
      c.removeEventListener("wheel", onWheel);
    };
  }, []);

  // Hit data for current frame
  const hitsRef = useRef({ stars: [], dsos: [] });

  // Click/hover handlers
  useEffect(() => {
    const c = canvasRef.current;
    if (!c) return;
    const onMove = (e) => {
      const rect = c.getBoundingClientRect();
      const x = e.clientX - rect.left,
        y = e.clientY - rect.top;
      const r2 = 100; // 10px radius
      const all = [...hitsRef.current.dsos, ...hitsRef.current.stars];
      let best = null,
        bestd = 1e9;
      for (const o of all) {
        const dx = o.x - x,
          dy = o.y - y;
        const d = dx * dx + dy * dy;
        if (d < r2 && d < bestd) {
          best = o;
          bestd = d;
        }
      }
      setHover(best);
    };
    const onClick = () => setPicked(hover);
    c.addEventListener("mousemove", onMove);
    c.addEventListener("click", onClick);
    return () => {
      c.removeEventListener("mousemove", onMove);
      c.removeEventListener("click", onClick);
    };
  }, [hover]);

  // Grid draw
  function drawGrid(ctx, w, h, transform, label) {
    ctx.save();
    ctx.strokeStyle = "rgba(255,255,255,0.12)";
    ctx.lineWidth = 1;
    const { scale, tx, ty } = transform;
    const toX = (ra) => ((360 - ((ra % 360 + 360) % 360)) / 360) * w * scale + tx;
    const toY = (dec) => ((1 - (dec + 90) / 180) * h) * scale + ty;
    for (let ra = 0; ra < 360; ra += 15) {
      ctx.beginPath();
      const x = toX(ra);
      ctx.moveTo(x, toY(-90));
      ctx.lineTo(x, toY(+90));
      ctx.stroke();
    }
    for (let d = -75; d <= 75; d += 15) {
      ctx.beginPath();
      ctx.moveTo(toX(0), toY(d));
      ctx.lineTo(toX(360), toY(d));
      ctx.stroke();
    }
    ctx.fillStyle = "rgba(255,255,255,0.7)";
    ctx.font = "12px ui-sans-serif, system-ui";
    for (let ra = 0; ra < 360; ra += 30) ctx.fillText(`${(ra / 15) | 0}h`, toX(ra) + 4, toY(+88));
    for (let d = -60; d <= 60; d += 30) ctx.fillText(`${d}°`, toX(355), toY(d) - 2);
    ctx.fillText(label, toX(355), toY(-85));
    ctx.restore();
  }

  // Animation
  useEffect(() => {
    let raf;
    const loop = (t) => {
      drawFrame(t);
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
  });

  const drawFrame = (t) => {
    const c = canvasRef.current;
    if (!c) return;
    const ctx = c.getContext("2d");
    const rect = c.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    if (c.width !== Math.floor(rect.width * dpr) || c.height !== Math.floor(rect.height * dpr)) {
      c.width = Math.floor(rect.width * dpr);
      c.height = Math.floor(rect.height * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }

    const w = rect.width,
      h = rect.height;
    const ra0 = animate ? (originRA + ((t * 0.003) % 360)) : originRA;
    ctx.fillStyle = "#02030a";
    ctx.fillRect(0, 0, w, h);
    if (coord === "galactic") {
      const grd = ctx.createLinearGradient(0, h * 0.45 + transform.ty, 0, h * 0.55 + transform.ty);
      grd.addColorStop(0, "rgba(255,255,255,0.03)");
      grd.addColorStop(0.5, "rgba(255,255,255,0.07)");
      grd.addColorStop(1, "rgba(255,255,255,0.03)");
      ctx.fillStyle = grd;
      ctx.fillRect(0, h * 0.35 + transform.ty, w, h * 0.3);
    }
    drawGrid(ctx, w, h, transform, coord === "equatorial" ? "RA/Dec" : "l/b (galactic)");

    const { scale, tx, ty } = transform;
    const toX = (ra) => (((360 - (((ra - ra0) % 360 + 360) % 360)) / 360) * w * scale + tx);
    const toY = (dec) => ((1 - (dec + 90) / 180) * h) * scale + ty;
    hitsRef.current = { stars: [], dsos: [] };
    const now = t / 1000;

    // DSOs first (so stars sit on top)
    if (layers.Galaxy || layers.Globular || layers.Open || layers.Nebula || layers.Planetary || layers.Double) {
      const kinds = ["Galaxy", "Globular", "Open", "Nebula", "Planetary", "Double"];
      for (const kind of kinds) {
        if (!layers[kind]) continue;
        const arr = dsos.filter((d) => d.kind === kind);
        for (let i = 0; i < arr.length; i++) {
          const o = arr[i];
          const x = toX(o.ra),
            y = toY(o.dec);
          const core = clamp((o.mag ? 6 - 0.5 * o.mag : 4), 1.0, maxCore);
          const halo = core * (1.8 + fuzz * 2.0);
          const tw = 0.85 + 0.25 * Math.sin(now * 2 + (o.ra * 0.1 + o.dec * 0.1));
          const col = dsoColor(o);
          const g = ctx.createRadialGradient(x, y, Math.max(0.1, core * 0.2), x, y, halo);
          let colCore = col;
          let colEdge = col;
          if (/^rgb\(/.test(col)) {
            const nums = col.match(/\d+/g).map(Number);
            colCore = `rgba(${nums[0]},${nums[1]},${nums[2]},0.95)`;
            colEdge = `rgba(${nums[0]},${nums[1]},${nums[2]},0)`;
          }
          g.addColorStop(0, colCore);
          g.addColorStop(1, colEdge);
          ctx.fillStyle = g;
          ctx.beginPath();
          ctx.arc(x, y, halo * tw, 0, Math.PI * 2);
          ctx.fill();
          ctx.fillStyle = colCore;
          (MARKERS[kind] || MARKERS.Other)(ctx, x, y, core * 0.6);
          hitsRef.current.dsos.push({ x, y, type: "dso", data: o });
        }
      }
    }

    // Stars (core + halo, spectral color)
    if (layers.Stars && stars.length) {
      for (let i = 0; i < stars.length; i++) {
        const s = stars[i];
        const x = toX(s.ra),
          y = toY(s.dec);
        const tw = 1 + 0.2 * Math.sin(now * 6 + (s.id ?? i));
        const r = Math.min(maxCore, magToCore(s.mag) * tw);
        const halo = r * (2.2 + fuzz * 2.5);
        const col = useSpectralColors ? spectralToColor(s.spectral) : "#ffffff";
        const [rr, gg, bb] = (col.match(/\d+/g) || [255, 255, 255]).map(Number);
        const g = ctx.createRadialGradient(x, y, Math.max(0.1, r * 0.2), x, y, halo);
        g.addColorStop(0, `rgba(${rr},${gg},${bb},0.55)`);
        g.addColorStop(1, `rgba(${rr},${gg},${bb},0.0)`);
        ctx.fillStyle = g;
        ctx.beginPath();
        ctx.arc(x, y, halo, 0, Math.PI * 2);
        ctx.fill();
        ctx.fillStyle = col;
        ctx.beginPath();
        ctx.arc(x, y, Math.max(0.5, r * 0.6), 0, Math.PI * 2);
        ctx.fill();
        hitsRef.current.stars.push({ x, y, type: "star", data: s });
      }
    }

    // Hover ring
    if (hover) {
      ctx.save();
      ctx.strokeStyle = "rgba(255,255,255,0.8)";
      ctx.lineWidth = 1.2;
      ctx.beginPath();
      ctx.arc(hover.x, hover.y, 8, 0, Math.PI * 2);
      ctx.stroke();
      ctx.restore();
    }
  };

  return (
    <div className="w-full h-[85vh] bg-black text-white relative rounded-2xl overflow-hidden shadow-xl">
      <canvas ref={canvasRef} className="w-full h-full block" />

      <div className="absolute top-3 left-3 bg-black/60 backdrop-blur px-3 py-2 rounded-xl border border-white/10 space-y-2">
        <div className="text-sm font-semibold">View</div>
        <div className="flex gap-2 text-sm">
          <button className={`px-2 py-1 rounded ${coord === "equatorial" ? "bg-white/20" : "bg-white/5"}`} onClick={() => window.setCoord && window.setCoord("equatorial")}>Equatorial</button>
          <button className={`px-2 py-1 rounded ${coord === "galactic" ? "bg-white/20" : "bg-white/5"}`} onClick={() => window.setCoord && window.setCoord("galactic")}>Galactic</button>
        </div>
        <div className="text-xs opacity-80">Rotate RA origin</div>
        <input type="range" min={0} max={359} value={originRA} onChange={(e) => setOriginRA(parseFloat(e.target.value))} className="w-48" />
        <div className="flex items-center gap-2 text-sm">
          <label className="flex items-center gap-1"><input type="checkbox" checked={animate} onChange={(e) => setAnimate(e.target.checked)} /> Animate</label>
          <button className="px-2 py-1 rounded bg-white/10" onClick={() => setTransform({ scale: 1, tx: 0, ty: 0 })}>Reset View</button>
        </div>
        <div className="flex items-center gap-2 text-sm mt-1">
          <label className="flex items-center gap-1"><input type="checkbox" checked={useSpectralColors} onChange={(e) => setUseSpectralColors(e.target.checked)} /> Spectral colors</label>
          <label className="flex items-center gap-2">Fuzz <input type="range" min={0} max={1} step={0.05} value={fuzz} onChange={(e) => setFuzz(parseFloat(e.target.value))} /></label>
          <label className="flex items-center gap-2">Max size <input type="range" min={1} max={8} step={0.1} value={maxCore} onChange={(e) => setMaxCore(parseFloat(e.target.value))} /></label>
        </div>
        <div className="grid grid-cols-2 gap-1 text-xs">
          {Object.keys(layers).map((k) => (
            <label key={k} className="flex items-center gap-1"><input type="checkbox" checked={layers[k]} onChange={(e) => setLayers((v) => ({ ...v, [k]: e.target.checked }))} /> {k}</label>
          ))}
        </div>
      </div>

      <InfoPanel picked={picked} setPicked={setPicked} coord={coord} />
    </div>
  );
}

// ---------------- Info Panel ----------------
function InfoPanel({ picked, setPicked, coord }) {
  if (!picked) return null;
  const d = picked.data;
  const isStar = picked.type === "star";
  const label = isStar ? `Star ${d.id ?? ""}` : d.name || d.kind;
  const ra = degToHMS(d.ra),
    dec = degToDMS(d.dec);
  return (
    <div className="absolute bottom-3 right-3 w-[380px] max-w-[95vw] bg-black/70 border border-white/10 rounded-xl p-3 space-y-2 backdrop-blur">
      <div className="flex items-center justify-between">
        <div className="font-semibold text-sm">{label}</div>
        <button className="text-xs opacity-80 hover:opacity-100" onClick={() => setPicked(null)}>Close</button>
      </div>
      <div className="text-xs grid grid-cols-2 gap-x-3 gap-y-1">
        <div className="opacity-70">Coords ({coord === "equatorial" ? "RA/Dec" : "l/b"})</div><div className="tabular-nums">{ra} , {dec}</div>
        {isStar && <><div className="opacity-70">Magnitude</div><div>{d.mag?.toFixed(2)}</div></>}
        {isStar && <><div className="opacity-70">Spectral</div><div>{d.spectral || "—"}</div></>}
        {!isStar && <><div className="opacity-70">Kind</div><div>{d.kind}</div></>}
        {!isStar && <><div className="opacity-70">Magnitude</div><div>{d.mag != null ? d.mag.toFixed(1) : "—"}</div></>}
        {!isStar && <><div className="opacity-70">Redshift</div><div>{d.z != null ? d.z.toExponential(3) : "—"}</div></>}
      </div>
      {d.desc && !isStar && <div className="text-xs opacity-80 leading-snug">{d.desc}</div>}
      {d.raw && <details className="text-[11px] opacity-60"><summary className="cursor-pointer">Raw line</summary><pre className="whitespace-pre-wrap">{d.raw}</pre></details>}
    </div>
  );
}

// ---------------- Uploader ----------------
function UploaderPanel({ setData }) {
  const [status, setStatus] = useState("Drop or choose a StarFinder text dump • or use demo");
  const [raw, setRaw] = useState("");
  const loadDemo = () => {
    setStatus("Loaded demo dataset");
    window.appSetData(DEMO);
  };
  const onFile = async (file) => {
    const text = await file.text();
    let parsed;
    try {
      const obj = JSON.parse(text);
      parsed = parseFromDatJson(obj);
      setStatus(`Loaded dat.json → ${parsed.stars.length} stars, ${parsed.dsos.length} DSOs`);
    } catch {
      parsed = parseFromRawText(text);
      setStatus(`Parsed text → ${parsed.stars.length} stars, ${parsed.dsos.length} DSOs`);
    }
    window.appSetData(parsed);
  };
  return (
    <div className="absolute top-3 right-3 bg-black/60 backdrop-blur px-3 py-2 rounded-xl border border-white/10 w-[360px] max-w-[92vw]">
      <div className="text-sm font-semibold mb-1">Load data</div>
      <div className="flex gap-2 items-center text-sm mb-2">
        <input type="file" accept=".txt,.json" onChange={(e) => e.target.files && onFile(e.target.files[0])} className="text-xs" />
        <button className="px-2 py-1 rounded bg-white/10" onClick={loadDemo}>Use Demo</button>
      </div>
      <textarea value={raw} onChange={(e) => setRaw(e.target.value)} placeholder="Paste raw catalog text here..." className="w-full h-24 bg-white/5 rounded p-2 text-xs"></textarea>
      <div className="flex gap-2 mt-2">
        <button className="px-2 py-1 rounded bg-white/10 text-sm" onClick={() => { const parsed = parseFromRawText(raw); setStatus(`Parsed ${parsed.stars.length} stars, ${parsed.dsos.length} DSOs`); window.appSetData(parsed); }}>Parse Pasted</button>
        <span className="text-xs opacity-80 self-center">{status}</span>
      </div>
    </div>
  );
}

// ---------------- Demo data ----------------
const DEMO = {
  stars: [
    { id: 1, ra: 37.95, dec: 89.26, mag: 2.0, spectral: "F7" }, // Polaris-ish
    { id: 2, ra: 101.287, dec: -16.716, mag: -1.46, spectral: "A1" }, // Sirius-ish
    { id: 3, ra: 95.988, dec: -52.696, mag: -0.72, spectral: "A9" }, // Canopus-ish
    { id: 4, ra: 79.172, dec: 45.997, mag: 0.08, spectral: "G3" }, // Capella-ish
    { id: 5, ra: 83.822, dec: -5.391, mag: 0.18, spectral: "B8" }, // Rigel-ish
    { id: 6, ra: 88.793, dec: 7.407, mag: 0.5, spectral: "M2" }, // Betelgeuse-ish
  ],
  dsos: [
    { name: "M31", ra: 10.6847, dec: 41.269, kind: "Galaxy", mag: 3.4, z: 0.001 },
    { name: "M13", ra: 250.423, dec: 36.461, kind: "Globular", mag: 5.8 },
    { name: "M45", ra: 56.75, dec: 24.1167, kind: "Open", mag: 1.6 },
    { name: "M42", ra: 83.822, dec: -5.391, kind: "Nebula", mag: 4.0 },
    { name: "M57", ra: 283.396, dec: 33.03, kind: "Planetary", mag: 8.8 },
  ],
};

// ---------------- Main App ----------------
export default function App() {
  const [coord, setCoord] = useState("equatorial");
  const [data, setData] = useState(DEMO);
  useEffect(() => { window.setCoord = setCoord; window.appSetData = setData; }, []);

  // Self-tests to validate parsers
  useEffect(() => {
    try {
      const raw = [
        "123 00:00:00 +00:00:00 5.0 A0V",
        "NGC 1 00:00:00 +00:00:00 10 5 X z=0.01",
      ].join("\n");
      const p1 = parseFromRawText(raw);
      console.assert(p1.stars.length === 1 && p1.dsos.length === 1, "raw parser");
      const obj = { A: "123 00:00:00 +00:00:00 5.0 K5III", B: "NGC 1 00:00:00 +00:00:00 10 5 X cz=1500km/s" };
      const p2 = parseFromDatJson(obj);
      console.assert(p2.stars.length === 1 && p2.dsos.length === 1, "json parser");
      // Extra tests
      console.assert(/^rgb\(/.test(spectralToColor("A0V")), "spectral color output");
      console.assert(/^rgba\(/.test(dsoColor({ kind: "Galaxy", z: 0.02 })), "dso redshift color");
    } catch (e) {
      console.warn("Self-tests failed", e);
    }
  }, []);

  return (
    <div className="min-h-screen bg-gradient-to-b from-zinc-900 to-black text-zinc-100 p-4">
      <div className="max-w-6xl mx-auto space-y-4">
        <header className="flex items-center justify-between">
          <h1 className="text-xl font-semibold tracking-wide">Interactive Star Map</h1>
          <div className="text-xs opacity-80">Pan/zoom: drag + wheel • RA increases to the left • Click objects for details</div>
        </header>
        <StarCanvas data={data} coord={coord} />
        <UploaderPanel />
        <footer className="text-xs opacity-80 flex items-center justify-between">
          <div>Stars colored by spectral class; DSOs colored by redshift/type. Toggle layers or switch to galactic to see the Milky Way band.</div>
          <div className="opacity-60">J2000 transforms; heuristic parser using your files.</div>
        </footer>
      </div>
    </div>
  );
}
