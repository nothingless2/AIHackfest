import React from 'react';
import {AbsoluteFill, Img, useCurrentFrame, useVideoConfig, delayRender, continueRender} from 'remotion';
import {bacaTema, gradien, muatFont} from './tema.js';
import {susunBaris} from './layout.js';

// Carousel (scripts/carousel.py): 1 frame = 1 slide. Isi slide, gambar, dan ZONA teks diputuskan
// Python (zona menjauhi wajah & zona UI TikTok); di sini hanya digambar. Tema = preset gaya yang
// sama dengan video, jadi carousel dan video satu akun terlihat satu keluarga.
// `hanyaTeks`: lapisan teks saja di atas latar transparan -- dipakai Python untuk MENGUKUR (teks di
// luar kotak aman, di zona UI, di atas wajah), bukan untuk dikirim.

const FONT = {family: 'Montserrat ExtraBold', file: 'fonts/Montserrat-ExtraBold.ttf', weight: 800};
const ISI = '"Inter", "DejaVu Sans", sans-serif';
const EMOJI = '"Noto Color Emoji"';

// Luminans relatif WCAG dari hex #RRGGBB.
const luminans = (hex) => {
  const k = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255)
    .map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  return 0.2126 * k[0] + 0.7152 * k[1] + 0.0722 * k[2];
};

const palet = (T) => {
  const w = T.w;
  const latar = (w.kartu || '#0E0B1C').slice(0, 7);
  const aksen = w.aksen ?? '#8B5CF6';
  const aksen2 = w.aksen2 ?? '#EC4899';
  // Warna `kunci` preset dibuat untuk teks DI ATAS VIDEO (mis. bersih = putih); di latar carousel
  // terang ia bisa tak terlihat (terukur 1 Okt: angka statistik kontras 1,0). Latar terang -> aksen.
  const terang = luminans(latar) > 0.5;
  return {
    latar,
    teks: w.teks ?? '#FFFFFF',
    sub: w.teks_sub ?? (w.teks ? w.teks : '#D6D3F0'),
    aksen,
    aksen2,
    sorot: w.sorot ?? '#C4B5FD',
    kunci: terang ? gradien([aksen, aksen2]) : gradien(w.kunci || ['#FFF4B8', '#F7CC55', '#C98E22']),
    r: (px) => px * T.sudut,
  };
};

const bersih = (k) => k.toLowerCase().replace(/[^\p{L}\p{N}_]/gu, '');

const Judul = ({teks, sorot, F, p, lebar, maks, rata = 'left'}) => {
  const tata = susunBaris({text: teks, lebar, fontFamily: F.family, fontWeight: F.weight, maks});
  return (
    <div style={{fontFamily: `"${F.family}", ${EMOJI}, sans-serif`, fontWeight: F.weight, textAlign: rata,
      lineHeight: 1.08, color: p.teks}}>
      {tata.baris.map((b, i) => (
        <div key={i} style={{fontSize: tata.ukuran, whiteSpace: 'nowrap'}}>
          {b.map((k, j) => (
            <span key={j} style={{color: sorot && bersih(k) === bersih(sorot) ? p.sorot : p.teks}}>
              {(j ? ' ' : '') + k}
            </span>
          ))}
        </div>
      ))}
    </div>
  );
};

const Paragraf = ({teks, ukuran, warna, berat = 500}) => (
  <div style={{fontFamily: ISI, fontWeight: berat, fontSize: ukuran, lineHeight: 1.35, color: warna}}>{teks}</div>
);

// Perkiraan tinggi isi tiap jenis pada skala penuh (kelipatan lebar slide). Kotak aman yang lebih
// pendek -- mis. di bawah wajah pada foto -- mengecilkan semua huruf sebanding, supaya teks tidak
// meluap ke atas wajah (terukur 1 Okt: judul hook menimpa dagu di slide berfoto).
const KEBUTUHAN = {hook: 0.66, isi: 0.62, daftar: 0.78, statistik: 0.5, kutipan: 0.62, cta: 0.6};

const Slide = ({s, i, n, W, H, F, p, kotak, hanyaTeks, platform}) => {
  const lebar = kotak.w;
  const skala = Math.min(1, kotak.h / (W * (KEBUTUHAN[s.jenis] || 0.62)));
  const besar = W * (s.jenis === 'hook' ? 0.13 : 0.095) * skala;
  const isi = W * 0.05 * skala;
  let badan = null;
  if (s.jenis === 'hook') {
    badan = (
      <>
        <Judul teks={s.judul} sorot={s.sorot} F={F} p={p} lebar={lebar} maks={besar} />
        {s.sub ? <div style={{marginTop: H * 0.025}}><Paragraf teks={s.sub} ukuran={isi * 1.05} warna={p.sub} /></div> : null}
      </>
    );
  } else if (s.jenis === 'isi') {
    badan = (
      <>
        <div style={{width: W * 0.09, height: W * 0.014, borderRadius: W * 0.007, background: p.aksen,
          marginBottom: H * 0.025}} />
        <Judul teks={s.judul} F={F} p={p} lebar={lebar} maks={besar} />
        <div style={{marginTop: H * 0.025}}><Paragraf teks={s.isi} ukuran={isi} warna={p.sub} /></div>
      </>
    );
  } else if (s.jenis === 'daftar') {
    badan = (
      <>
        <Judul teks={s.judul} F={F} p={p} lebar={lebar} maks={besar} />
        <div style={{marginTop: H * 0.03, display: 'flex', flexDirection: 'column', gap: H * 0.022}}>
          {s.butir.map((b, k) => (
            <div key={k} style={{display: 'flex', alignItems: 'flex-start', gap: W * 0.035}}>
              <div style={{flex: 'none', width: isi * 1.7, height: isi * 1.7, borderRadius: '50%', background: p.aksen,
                color: '#FFFFFF', fontFamily: ISI, fontWeight: 800, fontSize: isi * 0.9, display: 'flex',
                alignItems: 'center', justifyContent: 'center'}}>{k + 1}</div>
              <Paragraf teks={b} ukuran={isi} warna={p.teks} berat={600} />
            </div>
          ))}
        </div>
      </>
    );
  } else if (s.jenis === 'statistik') {
    badan = (
      <>
        <div style={{fontFamily: `"${F.family}", sans-serif`, fontWeight: F.weight, fontSize: W * 0.26 * skala, lineHeight: 1,
          backgroundImage: p.kunci, WebkitBackgroundClip: 'text', backgroundClip: 'text', color: 'transparent',
          whiteSpace: 'nowrap'}}>{s.angka}</div>
        <div style={{marginTop: H * 0.025}}><Paragraf teks={s.label} ukuran={isi * 1.1} warna={p.teks} berat={600} /></div>
      </>
    );
  } else if (s.jenis === 'kutipan') {
    badan = (
      <>
        <div style={{fontFamily: `"${F.family}", serif`, fontWeight: F.weight, fontSize: W * 0.3 * skala, lineHeight: 0.75,
          height: W * 0.16 * skala, color: p.aksen}}>“</div>
        <Judul teks={s.teks} F={F} p={p} lebar={lebar} maks={W * 0.075 * skala} />
        {s.oleh ? <div style={{marginTop: H * 0.025}}><Paragraf teks={`— ${s.oleh}`} ukuran={isi * 0.85} warna={p.sub} /></div> : null}
      </>
    );
  } else {
    badan = (
      <>
        <Judul teks={s.judul} F={F} p={p} lebar={lebar} maks={besar} />
        {s.sub ? <div style={{marginTop: H * 0.022}}><Paragraf teks={s.sub} ukuran={isi} warna={p.sub} /></div> : null}
        <div style={{marginTop: H * 0.035, alignSelf: 'flex-start', padding: `${H * 0.014}px ${W * 0.05}px`,
          borderRadius: p.r(999), background: `linear-gradient(135deg, ${p.aksen}, ${p.aksen2})`, color: '#FFFFFF',
          fontFamily: ISI, fontWeight: 800, fontSize: isi * 0.95}}>{s.tombol || 'Simpan & bagikan'}</div>
      </>
    );
  }
  const latar = hanyaTeks ? 'transparent' : p.latar;
  return (
    <AbsoluteFill style={{background: latar}}>
      {!hanyaTeks && s.gambar ? (
        <>
          <Img src={s.gambar} style={{width: W, height: H, objectFit: 'cover'}} />
          {/* Gradien gelap di sisi teks: kontrasnya diukur Python dari render akhir. */}
          <AbsoluteFill style={{background: kotak.zona === 'atas'
            ? 'linear-gradient(to bottom, rgba(0,0,0,0.82) 0%, rgba(0,0,0,0.6) 45%, rgba(0,0,0,0) 75%)'
            : 'linear-gradient(to top, rgba(0,0,0,0.86) 0%, rgba(0,0,0,0.62) 45%, rgba(0,0,0,0) 75%)'}} />
        </>
      ) : null}
      {!hanyaTeks && !s.gambar ? (
        <div style={{position: 'absolute', right: -W * 0.25, top: -W * 0.25, width: W * 0.8, height: W * 0.8,
          borderRadius: '50%', background: `radial-gradient(closest-side, ${p.aksen}55, transparent)`}} />
      ) : null}
      <div style={{position: 'absolute', left: kotak.x, top: kotak.y, width: kotak.w, height: kotak.h,
        display: 'flex', flexDirection: 'column',
        justifyContent: kotak.zona === 'atas' ? 'flex-start' : kotak.zona === 'bawah' ? 'flex-end' : 'center'}}>
        {badan}
      </div>
      {/* Nomor slide: di dalam kotak aman, pojok atas. */}
      <div style={{position: 'absolute', left: kotak.x, top: H * 0.045, fontFamily: ISI, fontWeight: 700,
        fontSize: W * 0.03, color: s.gambar && !hanyaTeks ? '#FFFFFF' : p.aksen, letterSpacing: 1}}>
        {`${i + 1}/${n}`}{platform === 'ig' && i === 0 ? '   geser →' : ''}
      </div>
    </AbsoluteFill>
  );
};

export const Carousel = ({slides, kotak, tema, hanyaTeks = false, platform = 'ig'}) => {
  const frame = useCurrentFrame();
  const {width, height} = useVideoConfig();
  const T = bacaTema(tema);
  const F = T.font || FONT;
  const p = palet(T);
  const [siap, setSiap] = React.useState(false);
  React.useEffect(() => {
    const h = delayRender(`memuat font ${F.family}`);
    muatFont(F).then(() => { setSiap(true); continueRender(h); }).catch((e) => { throw e; });
  }, []);
  if (!siap) return null;
  const i = Math.min(frame, slides.length - 1);
  return <Slide s={slides[i]} i={i} n={slides.length} W={width} H={height} F={F} p={p}
    kotak={kotak[i]} hanyaTeks={hanyaTeks} platform={platform} />;
};
