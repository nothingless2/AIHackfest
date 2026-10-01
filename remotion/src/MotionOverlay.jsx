import React from 'react';
import {AbsoluteFill, Sequence, spring, interpolate, useCurrentFrame, useVideoConfig,
  delayRender, continueRender} from 'remotion';
import {susunBaris} from './layout.js';
import {bacaTema, pegas, muatFont} from './tema.js';

// Motion graphic penjelas konteks, gaya video referensi user (24 Sep): kartu kaca gelap,
// cahaya ungu, kata kunci disorot. KATALOG TERTUTUP -- jenis & waktu divalidasi
// scripts/motion_plan.py; di sini hanya digambar. Semua di luar zona teks narasi (58-74% tinggi).
// Setelah masukFrames elemen DIAM (nilai animasi tepat 1): Python hanya merender frame masuk,
// lalu SATU gambar diam untuk sisanya; animasi keluar = pudar alpha oleh ffmpeg pada gambar diam
// itu (scripts/overlay_remotion.py). Karena itu keluar di sini HANYA boleh mengubah opasitas.

const FONT = {family: 'Montserrat ExtraBold', file: 'fonts/Montserrat-ExtraBold.ttf', weight: 800};
const EMOJI = '"Noto Color Emoji"';

// Palet dari tema (src/tema.js). Token yang tidak disebut preset jatuh ke literal bawaan di sini;
// `sub` sengaja undefined tanpa tema: tiap tempat memakai warna sub lamanya sendiri.
const warna = (aksen, T) => {
  const w = T.w;
  const a = w.aksen ?? aksen;
  return {
    aksen: a,
    aksen2: w.aksen2 ?? '#EC4899',
    sorot: w.sorot ?? '#C4B5FD',
    kaca: w.kartu ?? 'rgba(14, 11, 28, 0.80)',
    kacaTipis: w.kartu ?? 'rgba(14,11,28,0.55)',
    garis: w.garis ?? 'rgba(255, 255, 255, 0.16)',
    teks: w.teks ?? '#FFFFFF',
    sub: w.teks_sub,
    // Bayangan pendek saja: box-shadow ber-blur besar mahal di Chromium tanpa GPU (terukur
    // 0,46 dtk/frame). Cahaya lebar dibuat dengan gradien radial (Cahaya) yang murah.
    // Tema tanpa cahaya (mis. "bersih"): bayangan jatuh biasa, tanpa pendar warna.
    glow: T.cahaya ? `0 0 18px ${a}AA, 0 10px 24px rgba(0,0,0,0.45)` : '0 10px 24px rgba(0,0,0,0.30)',
    cahaya: T.cahaya,
    r: (px) => px * T.sudut,
    gerak: T.gerak,
  };
};

const Cahaya = ({c, lebar, tinggi}) => (c.cahaya ? (
  <div style={{position: 'absolute', left: '50%', top: '50%', width: lebar, height: tinggi,
    transform: 'translate(-50%, -50%)', pointerEvents: 'none', zIndex: -1,
    background: `radial-gradient(closest-side, ${c.aksen}99, ${c.aksen}33 55%, transparent 100%)`}} />
) : null);

const useGerak = (masukFrames, keluarFrames, dur, gerak) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const m = frame >= masukFrames ? 1
    : spring({frame, fps, durationInFrames: masukFrames, config: pegas({damping: 14, stiffness: 120, mass: 0.8}, gerak)});
  const k = interpolate(frame, [dur - keluarFrames, dur], [1, 0], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  const op = Math.min(interpolate(m, [0, 0.35], [0, 1], {extrapolateRight: 'clamp'}), k);
  return {m, k, op};
};

const Emoji = ({e, ukuran}) => (e ? <div style={{fontFamily: EMOJI, fontSize: ukuran, lineHeight: 1.1}}>{e}</div> : null);

// Teks multi-baris dengan satu kata disorot warna (kartu pembuka).
const TeksSorot = ({teks, sorot, lebar, maks, c, F}) => {
  const tata = React.useMemo(() => susunBaris({text: teks, lebar, fontFamily: F.family,
    fontWeight: F.weight, maks}), [teks]);
  const bersih = (w) => w.toLowerCase().replace(/[^\p{L}\p{N}_]/gu, '');
  return (
    <div style={{textAlign: 'center'}}>
      {tata.baris.map((b, i) => (
        <div key={i} style={{fontSize: tata.ukuran, lineHeight: 1.15}}>
          {b.map((w, j) => (
            <span key={j} style={{color: sorot && bersih(w) === sorot ? c.sorot : c.teks,
              marginRight: tata.ukuran * 0.26, display: 'inline-block'}}>{w}</span>
          ))}
        </div>
      ))}
    </div>
  );
};

const Kartu = ({c, lebar, children, style}) => (
  <div style={{position: 'relative', isolation: 'isolate', width: lebar}}>
    <Cahaya c={c} lebar={lebar * 1.35} tinggi="160%" />
    <div style={{background: c.kaca, border: `2px solid ${c.garis}`, borderRadius: c.r(48),
      boxShadow: c.glow, padding: '44px 52px', width: lebar, boxSizing: 'border-box',
      display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 18, ...style}}>
      {children}
    </div>
  </div>
);

const Redup = ({op}) => (
  <AbsoluteFill style={{opacity: op, background:
    'linear-gradient(to bottom, rgba(0,0,0,0.62) 0%, rgba(0,0,0,0.42) 40%, rgba(0,0,0,0) 57%)'}} />
);

const KartuHook = ({it, g, W, H, c, F}) => (
  <AbsoluteFill>
    <Redup op={g.op} />
    <AbsoluteFill style={{alignItems: 'center', paddingTop: H * 0.13}}>
      <div style={{opacity: g.op, transform: `translateY(${(1 - g.m) * 70}px) scale(${interpolate(g.m, [0, 1], [0.86, 1])})`}}>
        <Kartu c={c} lebar={W * 0.86}>
          <Emoji e={it.emoji} ukuran={W * 0.1} />
          <TeksSorot teks={it.teks} sorot={it.sorot} lebar={W * 0.72} maks={W * 0.095} c={c} F={F} />
        </Kartu>
      </div>
    </AbsoluteFill>
  </AbsoluteFill>
);

const KartuCta = ({it, g, W, H, c, F}) => (
  <AbsoluteFill>
    <Redup op={g.op} />
    <AbsoluteFill style={{alignItems: 'center', paddingTop: H * 0.15}}>
      <div style={{opacity: g.op, transform: `translateY(${(1 - g.m) * -60}px) scale(${interpolate(g.m, [0, 1], [0.9, 1])})`}}>
        <Kartu c={c} lebar={W * 0.84}>
          <TeksSorot teks={it.teks} sorot="" lebar={W * 0.7} maks={W * 0.085} c={c} F={F} />
          {it.sub ? <div style={{fontSize: W * 0.042, color: c.sub ?? '#D6D3F0'}}>{it.sub}</div> : null}
          <Emoji e={it.emoji} ukuran={W * 0.09} />
        </Kartu>
      </div>
    </AbsoluteFill>
  </AbsoluteFill>
);

const Sorot = ({it, g, W, H, c, Y}) => (
  <AbsoluteFill style={{alignItems: 'center', paddingTop: H * Y.sorot}}>
    <div style={{opacity: g.op, transform: `scale(${interpolate(g.m, [0, 1], [0.3, 1])}) rotate(${(1 - g.m) * -5}deg)`,
      background: `linear-gradient(135deg, ${c.aksen}, ${c.aksen2})`, borderRadius: 999,
      padding: `${W * 0.018}px ${W * 0.05}px`, boxShadow: c.glow, color: '#FFF',
      fontSize: W * 0.065, textTransform: 'uppercase', letterSpacing: 1, whiteSpace: 'nowrap'}}>
      {it.emoji ? <span style={{fontFamily: EMOJI, marginRight: W * 0.02}}>{it.emoji}</span> : null}
      {it.teks}
    </div>
  </AbsoluteFill>
);

const Ikon = ({it, g, W, H, c, Y}) => (
  <AbsoluteFill style={{alignItems: 'center', paddingTop: H * Y.ikon}}>
    <div style={{opacity: g.op, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 22,
      transform: `translateY(${(1 - g.m) * 50}px)`}}>
      <div style={{width: W * 0.24, height: W * 0.24, borderRadius: '50%', background: c.kaca,
        border: `3px solid ${c.aksen}`, boxShadow: c.glow, display: 'flex', alignItems: 'center',
        justifyContent: 'center', transform: `scale(${g.m}) rotate(${(1 - g.m) * 25}deg)`}}>
        <Emoji e={it.emoji} ukuran={W * 0.12} />
      </div>
      <div style={{color: c.teks, fontSize: W * 0.055, textShadow: '0 4px 18px rgba(0,0,0,0.85)',
        background: c.kacaTipis, borderRadius: c.r(24), padding: '8px 26px'}}>{it.teks}</div>
      {it.sub ? <div style={{color: c.sub ?? '#E4E1F7', fontSize: W * 0.036, textShadow: '0 3px 12px rgba(0,0,0,0.9)'}}>{it.sub}</div> : null}
    </div>
  </AbsoluteFill>
);

const Langkah = ({it, g, W, H, c, Y}) => (
  <AbsoluteFill style={{alignItems: 'center', paddingTop: H * Y.langkah}}>
    <div style={{opacity: g.op, transform: `translateY(${(1 - g.m) * -90}px)`, position: 'relative'}}>
      {/* Tanpa penanda "1/2": di konten user angkanya tidak bermakna (permintaan user 25 Sep). */}
      <Kartu c={c} lebar={W * 0.74} style={{padding: '40px 44px'}}>
        <div style={{color: c.teks, fontSize: W * 0.07, background: 'rgba(255,255,255,0.10)',
          border: '2px solid rgba(255,255,255,0.32)', borderBottomWidth: 6, borderRadius: c.r(22),
          padding: '10px 34px', textAlign: 'center'}}>
          {it.emoji ? <span style={{fontFamily: EMOJI, marginRight: 16}}>{it.emoji}</span> : null}{it.teks}
        </div>
        {it.sub ? <div style={{color: c.sub ?? '#CFCBE8', fontSize: W * 0.036, textAlign: 'center'}}>{it.sub}</div> : null}
      </Kartu>
    </div>
  </AbsoluteFill>
);

const Label = ({it, g, W, H, c, Y}) => (
  <AbsoluteFill style={{paddingTop: H * Y.label, paddingLeft: W * 0.06}}>
    <div style={{opacity: g.op, transform: `translateX(${(1 - g.m) * -W * 0.7}px)`, display: 'flex',
      alignItems: 'stretch', maxWidth: W * 0.86}}>
      <div style={{width: 12, borderRadius: 6, background: c.aksen, boxShadow: c.glow, marginRight: 22}} />
      <div style={{background: c.kaca, borderRadius: c.r(22), padding: '18px 30px', border: `2px solid ${c.garis}`}}>
        <div style={{color: c.teks, fontSize: W * 0.052}}>
          {it.emoji ? <span style={{fontFamily: EMOJI, marginRight: 14}}>{it.emoji}</span> : null}{it.teks}
        </div>
        {it.sub ? <div style={{color: c.sub ?? '#CFCBE8', fontSize: W * 0.034, marginTop: 6}}>{it.sub}</div> : null}
      </div>
    </div>
  </AbsoluteFill>
);

// Posisi vertikal (fraksi tinggi) per tata letak. 'atas' = bawaan (narasi AI: gambar biasanya
// suasana). 'bawah' = mode suara asli: pembicara biasanya di layar dan wajahnya di sepertiga
// atas -- storyboard 25 Sep memperlihatkan kartu langkah MENUTUPI WAJAH. Elemen penjelas
// diturunkan ke atas zona subtitle (karaoke ±76-84%); kartu pembuka/ajakan tetap (hanya 2 dtk).
export const POSISI = {
  atas: {sorot: 0.44, ikon: 0.17, langkah: 0.14, label: 0.47},
  bawah: {sorot: 0.58, ikon: 0.42, langkah: 0.47, label: 0.61},
};

// Kartu logo merek yang DIUCAPKAN (29 Sep, contoh video user): kartu putih membulat berisi ikon
// berwarna merek + namanya, melayang di samping kepala. Posisi (x, y) dihitung Python dari kotak
// wajah; ikon (path SVG + hex) dari simple-icons lewat daftar TERTUTUP config/merek_logo.json.
const Logo = ({it, g, W}) => {
  const lebar = W * 0.33;
  const ikon = lebar * 0.42;
  return (
    <div style={{position: 'absolute', left: it.x, top: it.y, width: lebar,
      background: '#FFFFFF', borderRadius: lebar * 0.22, padding: lebar * 0.08,
      boxShadow: '0 14px 30px rgba(0,0,0,0.28)', display: 'flex', flexDirection: 'column',
      alignItems: 'center', gap: lebar * 0.05, opacity: g.op,
      scale: String(interpolate(g.m, [0, 1], [0.55, 1])),
      translate: `0 ${(1 - g.m) * lebar * 0.18}px`}}>
      <svg role="img" viewBox="0 0 24 24" width={ikon} height={ikon} aria-label={it.merek}>
        <path d={it.path} fill={`#${it.hex}`} />
      </svg>
      <div style={{fontSize: lebar * 0.115, color: '#17171B', lineHeight: 1.1, textAlign: 'center'}}>
        {it.merek}
      </div>
    </div>
  );
};

const KOMPONEN = {kartu_hook: KartuHook, kartu_cta: KartuCta, sorot: Sorot, ikon: Ikon,
  langkah: Langkah, label: Label, logo: Logo};

const Elemen = ({it, dur, W, H, c, Y, F}) => {
  const g = useGerak(it.masukFrames ?? 16, it.keluarFrames ?? 8, dur, c.gerak);
  const K = KOMPONEN[it.jenis];
  return K ? <K it={it} g={g} W={W} H={H} c={c} Y={Y} F={F} /> : null;
};

export const MotionOverlay = ({items, aksen, tata, tema}) => {
  const {fps, width, height} = useVideoConfig();
  const T = bacaTema(tema);
  const F = T.font || FONT;
  const [siap, setSiap] = React.useState(false);
  React.useEffect(() => {
    const h = delayRender(`memuat font ${F.family}`);
    muatFont(F)
      .then(() => { setSiap(true); continueRender(h); })
      .catch((e) => { throw e; });
  }, []);
  if (!siap) return null;
  const c = warna(aksen || '#8B5CF6', T);
  return (
    <AbsoluteFill style={{fontFamily: `"${F.family}", ${EMOJI}, sans-serif`, fontWeight: F.weight}}>
      {items.map((it, i) => {
        const dari = Math.round(it.mulai * fps);
        const dur = Math.max(2, Math.round((it.selesai - it.mulai) * fps));
        return (
          <Sequence key={i} from={dari} durationInFrames={dur}>
            <Elemen it={it} dur={dur} W={width} H={height} c={c} Y={POSISI[tata] || POSISI.atas} F={F} />
          </Sequence>
        );
      })}
    </AbsoluteFill>
  );
};
