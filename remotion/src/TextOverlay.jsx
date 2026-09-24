import React from 'react';
import {AbsoluteFill, Sequence, spring, interpolate, useCurrentFrame, useVideoConfig,
  staticFile, delayRender, continueRender} from 'remotion';
import {loadFont} from '@remotion/fonts';
import {FONTS} from './fonts.js';
import {susunBaris} from './layout.js';

const EMOJI_FONT = '"Noto Color Emoji"';

// Satu kata dengan animasi masuk (pegas) dan keluar. Emoji ikut dianimasikan sebagai kata.
// JEDA_KATA (3) dan jumlah frame keluar HARUS sama dengan scripts/overlay_remotion.py: di sana
// diputuskan frame mana yang dirender Chromium dan mana yang memakai gambar diam.
const Kata = ({kata, index, dur, masukFrames, keluarFrames, gaya, animasi, fontFamily, fontWeight, ukuran}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const jeda = animasi === 'fade' || animasi === 'geser' ? 0 : index * 3;
  // Setelah masukFrames: TEPAT 1 (bukan 0,999..) supaya identik dengan gambar diam.
  const masuk = frame >= masukFrames ? 1
    : spring({frame: frame - jeda, fps, config: {damping: 11, stiffness: 140, mass: 0.7}});
  const keluar = interpolate(frame, [dur - keluarFrames, dur], [1, 0], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  const opasitas = Math.min(interpolate(masuk, [0, 0.4], [0, 1], {extrapolateRight: 'clamp'}), keluar);
  let transform = '';
  if (animasi === 'pop') transform = `scale(${interpolate(masuk, [0, 1], [0.4, 1])}) translateY(${(1 - masuk) * 40}px)`;
  else if (animasi === 'geser') transform = `translateY(${(1 - masuk) * 70}px)`;
  else if (animasi === 'fade') transform = `scale(${interpolate(masuk, [0, 1], [0.94, 1])})`;
  else transform = `translateY(${(1 - masuk) * 50}px) rotate(${(1 - masuk) * (index % 2 ? 6 : -6)}deg)`; // 'loncat'
  const adalahEmoji = /\p{Extended_Pictographic}/u.test(kata);
  return (
    <span style={{
      display: 'inline-block', opacity: opasitas, transform,
      transformOrigin: '50% 80%', marginRight: ukuran * 0.28,
      fontFamily: adalahEmoji ? EMOJI_FONT : `"${fontFamily}", ${EMOJI_FONT}, sans-serif`,
      fontWeight, fontSize: ukuran, lineHeight: 1.12, color: gaya.warna,
      WebkitTextStroke: adalahEmoji ? '0' : `${Math.max(3, ukuran * 0.07)}px ${gaya.garis}`,
      paintOrder: 'stroke fill',
      textShadow: adalahEmoji ? 'none' : `0 ${ukuran * 0.06}px ${ukuran * 0.16}px rgba(0,0,0,0.55)`,
    }}>{kata}</span>
  );
};

const Item = ({item, cfg, font, lebar, tinggi}) => {
  const tata = React.useMemo(() => susunBaris({
    text: item.text,
    lebar: lebar * 0.84, fontFamily: font.family, fontWeight: font.weight,
    maks: tinggi * cfg.ukuranMaks, upper: font.upper,
  }), [item.text]);
  let idx = 0;
  return (
    <AbsoluteFill style={{justifyContent: cfg.justify, alignItems: 'center', paddingTop: cfg.padAtas, paddingBottom: cfg.padBawah}}>
      <div style={{textAlign: 'center', width: lebar * 0.9}}>
        {tata.baris.map((baris, bi) => (
          <div key={bi}>
            {baris.map((kata, ki) => (
              <Kata key={ki} kata={kata} index={idx++} dur={item.dur} gaya={cfg.gaya}
                masukFrames={item.masukFrames ?? 20} keluarFrames={item.keluarFrames ?? 9}
                animasi={cfg.animasi} fontFamily={font.family} fontWeight={font.weight} ukuran={tata.ukuran} />
            ))}
          </div>
        ))}
      </div>
    </AbsoluteFill>
  );
};

export const TextOverlay = ({items, posisi, font: namaFont, animasi, warna, garis, ukuranMaks}) => {
  const {fps, width, height} = useVideoConfig();
  const font = FONTS[namaFont] || FONTS.standar;
  const [siap, setSiap] = React.useState(!font.file);
  React.useEffect(() => {
    if (!font.file) return;
    const h = delayRender(`memuat font ${font.family}`);
    loadFont({family: font.family, url: staticFile(font.file), weight: String(font.weight)})
      .then(() => { setSiap(true); continueRender(h); })
      .catch((e) => { throw e; });
  }, []);
  if (!siap) return null;
  const cfg = {
    animasi: animasi || 'pop', ukuranMaks: ukuranMaks || 0.075,
    gaya: {warna: warna || '#FFFFFF', garis: garis || '#111111'},
    justify: posisi === 'atas' ? 'flex-start' : posisi === 'bawah' ? 'flex-end' : 'center',
    padAtas: posisi === 'atas' ? Math.round(height * 0.16) : 0,
    padBawah: posisi === 'bawah' ? Math.round(height * 0.2) : 0,
  };
  return (
    <AbsoluteFill>
      {items.map((it, i) => {
        const dari = Math.round(it.mulai * fps);
        const dur = Math.max(2, Math.round((it.selesai - it.mulai) * fps));
        return (
          <Sequence key={i} from={dari} durationInFrames={dur}>
            <Item item={{...it, dur}} cfg={cfg} font={font} lebar={width} tinggi={height} />
          </Sequence>
        );
      })}
    </AbsoluteFill>
  );
};
