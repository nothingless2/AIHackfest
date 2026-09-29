import React from 'react';
import {AbsoluteFill, Sequence, spring, interpolate, Easing, useCurrentFrame, useVideoConfig,
  staticFile, delayRender, continueRender} from 'remotion';
import {loadFont} from '@remotion/fonts';
import {fitText} from '@remotion/layout-utils';

// Caption dinamis (27 Sep, contoh video user): potongan 1-3 kata; SATU kata kunci besar bergradasi
// emas dengan pop, kata pendamping kecil di atasnya. Potongan & kata kunci diputuskan Python
// (scripts/caption_dinamis.py); di sini hanya digambar.
// Setelah masukFrames semua nilai animasi TEPAT final: Python merender frame masuk saja, lalu
// SATU gambar diam untuk sisa potongan (scripts/overlay_remotion.py rencana_caption).
// Animasi mengikuti Remotion Agent Skills (spring/interpolate + Easing.bezier, properti CSS
// `scale`/`translate`); Easing.spring/perceptual-scale belum ada di Remotion 4.0.250 kita.

const FONT = {family: 'Montserrat ExtraBold', file: 'fonts/Montserrat-ExtraBold.ttf', weight: 800};
const EMAS = 'linear-gradient(180deg, #FFF4B8 0%, #F7CC55 48%, #C98E22 100%)';
const BAYANG = 'drop-shadow(0 6px 10px rgba(0,0,0,0.55))';
// Varian "panggung" (29 Sep): di latar kertas terang, caption jadi JUDUL serif miring hitam
// (seperti "NGEDITVIDEO," di contoh video user); kata kunci diberi warna aksen.
const SERIF = {family: 'Instrument Serif', file: 'fonts/InstrumentSerif-Italic.ttf', weight: 400, style: 'italic'};
const AKSEN = '#E8743B';

const PotonganPanggung = ({it, W, H, y}) => {
  const frame = useCurrentFrame();
  const muncul = frame >= it.masukFrames ? 1 : interpolate(frame, [0, it.masukFrames], [0, 1], {
    extrapolateLeft: 'clamp', extrapolateRight: 'clamp', easing: Easing.bezier(0.16, 1, 0.3, 1)});
  const teks = it.kata.join(' ').toUpperCase();
  const ukuran = Math.min(H * 0.075, fitText({text: teks || 'x', withinWidth: W * 0.84,
    fontFamily: SERIF.family, fontWeight: String(SERIF.weight)}).fontSize);
  return (
    <div style={{position: 'absolute', left: 0, width: W, top: H * y, translate: `0 ${-50 + (1 - muncul) * 30}%`,
      opacity: muncul, textAlign: 'center', whiteSpace: 'pre', fontFamily: `"${SERIF.family}", serif`,
      fontStyle: 'italic', fontWeight: SERIF.weight, fontSize: ukuran, lineHeight: 1.0, color: '#17171B',
      letterSpacing: ukuran * -0.02}}>
      {it.kata.map((k, i) => (
        <span key={i} style={{color: i === it.kunci ? AKSEN : '#17171B'}}>
          {(i ? ' ' : '') + k.toUpperCase()}
        </span>
      ))}
    </div>
  );
};

const Potongan = ({it, W, H, y, lebarMaks}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const selesai = frame >= it.masukFrames;
  const pegas = selesai ? 1 : spring({frame, fps, durationInFrames: it.masukFrames,
    config: {damping: 9, stiffness: 180, mass: 0.6}});
  const muncul = selesai ? 1 : interpolate(frame, [0, Math.max(1, it.masukFrames * 0.6)], [0, 1], {
    extrapolateLeft: 'clamp', extrapolateRight: 'clamp', easing: Easing.bezier(0.16, 1, 0.3, 1)});
  const lebar = W * lebarMaks;
  const ada = it.kunci !== null && it.kunci !== undefined;
  const kunci = ada ? it.kata[it.kunci] : null;
  // Urutan baca dijaga: kata SEBELUM kata kunci di atasnya, kata SESUDAHNYA di bawahnya.
  const pendamping = ada ? it.kata.slice(0, it.kunci).join(' ') : it.kata.join(' ');
  const sesudah = ada ? it.kata.slice(it.kunci + 1).join(' ') : '';
  const ukur = (teks, maks) => Math.min(maks, fitText({text: teks || 'x', withinWidth: lebar,
    fontFamily: FONT.family, fontWeight: String(FONT.weight)}).fontSize);
  const kecil = Math.min(...[pendamping, sesudah].filter(Boolean)
    .map((t) => ukur(t, H * (ada ? 0.036 : 0.056))), H);
  const besar = ada ? ukur(kunci, H * 0.1) : 0;
  const teksPutih = {
    fontFamily: `"${FONT.family}", sans-serif`, fontWeight: FONT.weight, color: '#FFFFFF',
    lineHeight: 1.05, whiteSpace: 'pre', WebkitTextStroke: `${Math.max(2, kecil * 0.05)}px rgba(0,0,0,0.35)`,
    paintOrder: 'stroke fill', textShadow: `0 ${kecil * 0.08}px ${kecil * 0.25}px rgba(0,0,0,0.6)`,
  };
  return (
    <div style={{position: 'absolute', left: 0, width: W, top: H * y, translate: '0 -50%',
      display: 'flex', flexDirection: 'column', alignItems: 'center'}}>
      {pendamping ? (
        <div style={{...teksPutih, fontSize: kecil, opacity: muncul,
          translate: `0 ${(1 - muncul) * kecil * 0.5}px`,
          marginBottom: ada ? besar * -0.08 : 0}}>
          {pendamping}
        </div>
      ) : null}
      {ada ? (
        <div style={{filter: BAYANG, opacity: Math.min(1, pegas * 3),
          scale: String(interpolate(pegas, [0, 1], [0.6, 1]))}}>
          <div style={{fontFamily: `"${FONT.family}", sans-serif`, fontWeight: FONT.weight,
            fontSize: besar, lineHeight: 1.08, whiteSpace: 'pre', backgroundImage: EMAS,
            WebkitBackgroundClip: 'text', backgroundClip: 'text', color: 'transparent',
            letterSpacing: besar * -0.01}}>
            {kunci}
          </div>
        </div>
      ) : null}
      {sesudah ? (
        <div style={{...teksPutih, fontSize: kecil, opacity: muncul, marginTop: besar * -0.06,
          translate: `0 ${(1 - muncul) * kecil * -0.5}px`}}>
          {sesudah}
        </div>
      ) : null}
    </div>
  );
};

// ringkas: timeline RINGKAS untuk render -- potongan ke-j menempati frame [j*(M+1), j*(M+1)+M]:
// M frame masuk + 1 frame diam. Python memetakan frame ini ke waktu aslinya (daftar concat ffmpeg),
// jadi SATU sesi Chromium merender semua potongan dan komposit hanya mendapat SATU input.
export const CaptionDinamis = ({items, y = 0.7, lebarMaks = 0.74, ringkas = false, yPanggung = 0.365}) => {
  const {fps, width, height} = useVideoConfig();
  const [siap, setSiap] = React.useState(false);
  React.useEffect(() => {
    const h = delayRender(`memuat font ${FONT.family}`);
    Promise.all([
      loadFont({family: FONT.family, url: staticFile(FONT.file), weight: String(FONT.weight)}),
      loadFont({family: SERIF.family, url: staticFile(SERIF.file), weight: String(SERIF.weight),
        style: SERIF.style}),
    ])
      .then(() => { setSiap(true); continueRender(h); })
      .catch((e) => { throw e; });
  }, []);
  if (!siap) return null;
  return (
    <AbsoluteFill>
      {items.map((it, i) => {
        const dari = ringkas ? i * (it.masukFrames + 1) : Math.round(it.mulai * fps);
        const dur = ringkas ? it.masukFrames + 1 : Math.max(1, Math.round((it.selesai - it.mulai) * fps));
        return (
          <Sequence key={i} from={dari} durationInFrames={dur}>
            {it.varian === 'panggung'
              ? <PotonganPanggung it={it} W={width} H={height} y={yPanggung} />
              : <Potongan it={it} W={width} H={height} y={y} lebarMaks={lebarMaks} />}
          </Sequence>
        );
      })}
    </AbsoluteFill>
  );
};
