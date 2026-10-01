import React from 'react';
import {AbsoluteFill, Sequence, spring, interpolate, useCurrentFrame, useVideoConfig,
  delayRender, continueRender} from 'remotion';
import {bacaTema, pegas, muatFont} from './tema.js';

// Latar OPAK tata letak "panggung" (29 Sep, contoh video user): kertas terang berkisi, ilustrasi
// animasi di atas, bayangan kartu tempat video pembicara ditempel ffmpeg. Jendela, ilustrasi, dan
// posisi kartu diputuskan Python (motion_plan.jadwal_panggung, overlay_remotion.render_panggung).
// Timeline RINGKAS: jendela ke-i menempati frame [it.dari, it.dari + it.dur) -- semua frame
// dirender (ilustrasi bergerak sepanjang jendela), lalu dipetakan ke waktu asli lewat concat.
// Ilustrasi digambar sendiri (HTML/CSS), tanpa logo/maskot merek.

const FONT = {family: 'Montserrat ExtraBold', file: 'fonts/Montserrat-ExtraBold.ttf', weight: 800};
const KERTAS = '#F4F4F1';
const KISI = '#E4E4E0';
const AKSEN = '#E8743B';
const UNGU = '#6D5DF5';
const GELAP = '#1E1E24';

// Palet dari tema (src/tema.js); tanpa tema = konstanta di atas, persis seperti sebelumnya.
const palet = (T) => ({
  aksen: T.w.aksen ?? AKSEN,
  ungu: T.w.aksen2 ?? UNGU,
  kertas: T.w.latar ?? KERTAS,
  kisi: T.w.kisi ?? KISI,
  bayangAksen: T.w.aksen ? `${T.w.aksen}59` : 'rgba(232,116,59,0.35)',
  r: (px) => px * T.sudut,
  gerak: T.gerak,
  F: T.font || FONT,
});

const useMasuk = (durasi, gerak) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  return spring({frame, fps, durationInFrames: durasi, config: pegas({damping: 14, stiffness: 130}, gerak)});
};

const Panel = ({w, h, children, gelap, p}) => {
  const m = useMasuk(12, p.gerak);
  return (
    <div style={{width: w, height: h, borderRadius: p.r(28), background: gelap ? GELAP : '#FFFFFF',
      boxShadow: '0 8px 22px rgba(0,0,0,0.10)', border: '2px solid rgba(0,0,0,0.06)',
      opacity: Math.min(1, m * 2), scale: String(interpolate(m, [0, 1], [0.85, 1])),
      overflow: 'hidden', position: 'relative'}}>
      {children}
    </div>
  );
};

const Timeline = ({w, h, dur, p}) => {
  const frame = useCurrentFrame();
  const baris = [
    [{a: 0.02, b: 0.24, c: p.aksen}, {a: 0.6, b: 0.8, c: p.aksen}],
    [{a: 0.02, b: 0.3, c: GELAP}, {a: 0.32, b: 0.46, c: p.aksen}, {a: 0.48, b: 0.7, c: GELAP}, {a: 0.72, b: 0.96, c: GELAP}],
    [{a: 0.02, b: 0.98, c: p.ungu, gelombang: true}],
    [{a: 0.08, b: 0.34, c: '#8A8A93'}, {a: 0.5, b: 0.74, c: '#8A8A93'}],
  ];
  const kepala = interpolate(frame, [0, dur], [0.05, 0.92], {extrapolateRight: 'clamp'});
  const tinggi = (h - 90) / baris.length;
  return (
    <Panel w={w} h={h} p={p}>
      <div style={{height: 34, borderBottom: '2px solid #EEE', display: 'flex', gap: w * 0.2, padding: '6px 24px'}}>
        {[0, 1, 2, 3].map((i) => <div key={i} style={{width: 36, height: 10, borderRadius: 5, background: '#DDD', marginTop: 6}} />)}
      </div>
      {baris.map((r, i) => (
        <div key={i} style={{position: 'absolute', top: 44 + i * tinggi, left: 20, right: 20, height: tinggi - 14}}>
          {r.map((k, j) => (
            <div key={j} style={{position: 'absolute', left: `${k.a * 100}%`, width: `${(k.b - k.a) * 100}%`,
              height: '100%', borderRadius: 8, background: k.c, opacity: k.c === '#8A8A93' ? 0.55 : 1,
              backgroundImage: k.gelombang ? 'repeating-linear-gradient(90deg, rgba(255,255,255,0.55) 0 3px, transparent 3px 9px)' : 'none'}} />
          ))}
        </div>
      ))}
      <div style={{position: 'absolute', top: 36, bottom: 8, left: 20 + (w - 40) * kepala, width: 4, background: '#E23B3B'}}>
        <div style={{position: 'absolute', top: -6, left: -9, width: 22, height: 14, borderRadius: 4, background: '#E23B3B'}} />
      </div>
    </Panel>
  );
};

const GrafikNaik = ({w, h, p}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const nilai = [0.28, 0.45, 0.63, 0.9];
  return (
    <Panel w={w} h={h} p={p}>
      <div style={{position: 'absolute', left: 50, right: 50, bottom: 40, top: 40, display: 'flex',
        alignItems: 'flex-end', gap: 36}}>
        {nilai.map((v, i) => {
          const s = spring({frame: frame - 6 - i * 5, fps, config: pegas({damping: 13, stiffness: 120}, p.gerak)});
          return <div key={i} style={{flex: 1, height: `${v * 100 * s}%`, borderRadius: '14px 14px 4px 4px',
            background: i === nilai.length - 1 ? p.aksen : p.ungu, opacity: i === nilai.length - 1 ? 1 : 0.35 + i * 0.2}} />;
        })}
      </div>
    </Panel>
  );
};

const Checklist = ({w, h, p}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  return (
    <Panel w={w} h={h} p={p}>
      {[0, 1, 2].map((i) => {
        const s = spring({frame: frame - 8 - i * 9, fps, config: pegas({damping: 12, stiffness: 160}, p.gerak)});
        return (
          <div key={i} style={{position: 'absolute', left: 50, right: 50, top: 42 + i * (h - 60) / 3,
            height: (h - 60) / 3 - 22, display: 'flex', alignItems: 'center', gap: 30}}>
            <div style={{width: 64, height: 64, borderRadius: 16, border: `5px solid ${p.ungu}`,
              background: s > 0.5 ? p.ungu : 'transparent', display: 'flex', alignItems: 'center', justifyContent: 'center'}}>
              <div style={{width: 30, height: 16, borderLeft: '7px solid #FFF', borderBottom: '7px solid #FFF',
                rotate: '-45deg', translate: '0 -4px', scale: String(s)}} />
            </div>
            <div style={{flex: 1, height: 22, borderRadius: 11, background: '#E6E6EA', maxWidth: `${80 - i * 12}%`}} />
          </div>
        );
      })}
    </Panel>
  );
};

const Chat = ({w, h, p}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const gelembung = [{kanan: false, l: 0.62}, {kanan: true, l: 0.5}, {kanan: false, l: 0.7}];
  return (
    <Panel w={w} h={h} p={p}>
      {gelembung.map((g, i) => {
        const s = spring({frame: frame - 5 - i * 10, fps, config: pegas({damping: 12, stiffness: 150}, p.gerak)});
        return (
          <div key={i} style={{position: 'absolute', top: 36 + i * (h - 50) / 3, height: (h - 50) / 3 - 26,
            width: `${g.l * 100}%`, [g.kanan ? 'right' : 'left']: 36, borderRadius: 26,
            background: g.kanan ? p.ungu : '#ECECF1', opacity: Math.min(1, s * 2), scale: String(s),
            transformOrigin: g.kanan ? '100% 100%' : '0% 100%', display: 'flex', alignItems: 'center',
            gap: 12, padding: '0 28px'}}>
            {[0.5, 0.3, 0.15].map((x, j) => <div key={j} style={{height: 14, flex: x, borderRadius: 7,
              background: g.kanan ? 'rgba(255,255,255,0.7)' : '#C9C9D2'}} />)}
          </div>
        );
      })}
    </Panel>
  );
};

const Kode = ({w, h, dur, p}) => {
  const frame = useCurrentFrame();
  const warna = ['#F58B6B', '#8AB4F8', '#C3E88D', '#8AB4F8', '#F7C873', '#C3E88D'];
  const tampil = interpolate(frame, [4, Math.max(8, dur * 0.7)], [0, warna.length],
    {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  return (
    <Panel w={w} h={h} gelap p={p}>
      <div style={{display: 'flex', gap: 12, padding: '18px 24px'}}>
        {['#FF5F57', '#FEBC2E', '#28C840'].map((c) => <div key={c} style={{width: 18, height: 18, borderRadius: 9, background: c}} />)}
      </div>
      {warna.map((c, i) => {
        const bagian = Math.max(0, Math.min(1, tampil - i));
        return <div key={i} style={{marginLeft: 40 + (i % 3) * 36, marginTop: 14, height: 18, borderRadius: 9,
          width: `${(40 + ((i * 37) % 35)) * bagian}%`, background: c, opacity: 0.9}} />;
      })}
    </Panel>
  );
};

const KartuKata = ({w, h, teks, p}) => {
  const m = useMasuk(10, p.gerak);
  return (
    <div style={{width: w, height: h, display: 'flex', alignItems: 'center', justifyContent: 'center'}}>
      <div style={{padding: '34px 60px', borderRadius: p.r(32), background: p.aksen, color: '#FFFFFF',
        fontFamily: `"${p.F.family}", sans-serif`, fontWeight: p.F.weight,
        fontSize: Math.min(150, (w / Math.max(4, teks.length)) * 1.5),
        boxShadow: `0 10px 24px ${p.bayangAksen}`, rotate: `${interpolate(m, [0, 1], [-8, -3])}deg`,
        scale: String(interpolate(m, [0, 1], [0.5, 1])), opacity: Math.min(1, m * 2), textTransform: 'uppercase'}}>
        {teks}
      </div>
    </div>
  );
};

const ILUSTRASI = {timeline: Timeline, grafik_naik: GrafikNaik, checklist: Checklist, chat: Chat, kode: Kode, kata: KartuKata};

const Jendela = ({it, W, H, kartu, p}) => {
  const Ilus = ILUSTRASI[it.ilustrasi] || KartuKata;
  const w = W * 0.78;
  const h = H * 0.2;
  return (
    <AbsoluteFill style={{background: p.kertas,
      backgroundImage: `linear-gradient(${p.kisi} 2px, transparent 2px), linear-gradient(90deg, ${p.kisi} 2px, transparent 2px)`,
      backgroundSize: `${Math.round(W / 20)}px ${Math.round(W / 20)}px`}}>
      <div style={{position: 'absolute', left: (W - w) / 2, top: H * 0.075}}>
        <Ilus w={w} h={h} dur={it.dur} teks={it.teks || ''} p={p} />
      </div>
      {/* Bayangan kartu: video pembicara ditempel ffmpeg tepat di atasnya. */}
      <div style={{position: 'absolute', left: kartu.x, top: kartu.y, width: kartu.w, height: kartu.h,
        borderRadius: kartu.r, background: '#DADAD6', boxShadow: '0 14px 30px rgba(0,0,0,0.16)'}} />
    </AbsoluteFill>
  );
};

export const Panggung = ({items, kartu, tema}) => {
  const [siap, setSiap] = React.useState(false);
  const {width, height} = useVideoConfig();
  const p = palet(bacaTema(tema));
  React.useEffect(() => {
    const h = delayRender(`memuat font ${p.F.family}`);
    muatFont(p.F)
      .then(() => { setSiap(true); continueRender(h); })
      .catch((e) => { throw e; });
  }, []);
  if (!siap) return null;
  return (
    <AbsoluteFill>
      {items.map((it, i) => (
        <Sequence key={i} from={it.dari} durationInFrames={it.dur}>
          <Jendela it={it} W={width} H={height} kartu={kartu} p={p} />
        </Sequence>
      ))}
    </AbsoluteFill>
  );
};
