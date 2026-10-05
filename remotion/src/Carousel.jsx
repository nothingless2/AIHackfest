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
    // Latar terang: aksen PENUH. Gradien ke aksen2 (biasanya lebih muda) terukur kontras 2,7 di atas
    // latar foto + putih (2 Okt), di bawah ambang 3.
    kunci: terang ? gradien([aksen]) : gradien(w.kunci || ['#FFF4B8', '#F7CC55', '#C98E22']),
    r: (px) => px * T.sudut,
  };
};

const bersih = (k) => k.toLowerCase().replace(/[^\p{L}\p{N}_]/gu, '');

// Ikon: SVG INLINE, bukan berkas/unduhan -- tidak ada lisensi gambar dan tidak ada permintaan
// jaringan saat render. Semua digambar di kanvas 24x24 dengan garis (stroke), jadi warnanya ikut
// tema lewat `currentColor` dan tebalnya seragam. Kuncinya HARUS sama dengan carousel.IKON (dijaga tes).
const IKON = {
  lampu: 'M9 18h6M10 21h4M12 3a6 6 0 0 0-4 10.5c.6.7 1 1.5 1 2.5h6c0-1 .4-1.8 1-2.5A6 6 0 0 0 12 3Z',
  centang: 'M4 12.5 9.5 18 20 6',
  silang: 'M6 6l12 12M18 6 6 18',
  peringatan: 'M12 3 2.5 20h19L12 3Zm0 6v5m0 3v.5',
  roket: 'M5 15c-1.5 1.5-2 6-2 6s4.5-.5 6-2m-4-4a16 16 0 0 1 11-11c2 0 4 0 4 0s0 2 0 4a16 16 0 0 1-11 11l-4-4Zm9-6.5h.01',
  grafik: 'M3 21h18M6 17v-5m5 5V7m5 10v-8',
  jam: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Zm0-13v5l3.5 2',
  uang: 'M12 2v20M17 6.5C17 4.6 14.8 3.5 12 3.5S7 4.6 7 6.5 9.2 10 12 11s5 2 5 4-2.2 3.5-5 3.5-5-1.1-5-3',
  bintang: 'm12 3 2.7 5.8 6.3.8-4.6 4.4 1.2 6.2L12 17.3 6.4 20.2l1.2-6.2L3 9.6l6.3-.8L12 3Z',
  api: 'M12 22c3.9 0 7-2.9 7-6.5 0-4.5-4-6.5-4-10.5 0 0-3 1.5-3 5 0 0-2-1-2-3-2 1.5-5 4-5 8.5C5 19.1 8.1 22 12 22Z',
  orang: 'M12 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8Zm-8 10c0-4 3.6-6.5 8-6.5s8 2.5 8 6.5',
  obrolan: 'M21 12a8 8 0 0 1-8 8H7l-4 3V12a8 8 0 0 1 8-8h2a8 8 0 0 1 8 8Z',
  target: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Zm0-4.5a4.5 4.5 0 1 0 0-9 4.5 4.5 0 0 0 0 9Zm0-3.5h.01',
  gembok: 'M6 11h12v10H6V11Zm2.5 0V7.5a3.5 3.5 0 1 1 7 0V11',
  hati: 'M12 20.5 3.8 12.6a5 5 0 0 1 7-7.1l1.2 1.1 1.2-1.1a5 5 0 0 1 7 7.1L12 20.5Z',
  kunci: 'M14.5 10.5a4.5 4.5 0 1 0-4.1 2.7L9 14.6l-1.5.2.2 1.5-1.5.2.2 1.5-1.4.2-1.5-1.5 6-6a4.5 4.5 0 0 0 5-.2ZM16 8h.01',
};

const Ikon = ({nama, ukuran, warna, latar, radius}) => {
  const d = IKON[nama];
  if (!d) return null;
  return (
    <div style={{width: ukuran, height: ukuran, borderRadius: radius, background: latar,
      display: 'flex', alignItems: 'center', justifyContent: 'center', color: warna, flex: 'none'}}>
      <svg width={ukuran * 0.62} height={ukuran * 0.62} viewBox="0 0 24 24" fill="none"
        stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d={d} />
      </svg>
    </div>
  );
};

const Judul = ({teks, sorot, F, p, lebar, maks, rata = 'left'}) => {
  const tata = susunBaris({text: teks, lebar, fontFamily: F.family, fontWeight: F.weight, maks, ukurSemua: true});
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

const Slide = ({s, i, n, W, H, F, p, kotak, hanyaTeks, platform, watermark}) => {
  const lebar = kotak.w;
  const skala = Math.min(1, kotak.h / (W * (KEBUTUHAN[s.jenis] || 0.62)));
  const besar = W * (s.jenis === 'hook' ? 0.13 : 0.095) * skala;
  const isi = W * 0.05 * skala;
  // Lencana ikon: ukurannya ikut skala kotak aman, jadi di slide berfoto (kotak lebih pendek) ia
  // mengecil bersama huruf dan tidak mendorong teks keluar kotak.
  const lencana = s.stiker ? (
    <div style={{marginBottom: H * 0.022}}>
      <Img src={s.stiker} style={{width: W * 0.25 * skala, height: W * 0.25 * skala, objectFit: 'contain'}} />
    </div>
  ) : s.ikon ? (
    <div style={{marginBottom: H * 0.022}}>
      <Ikon nama={s.ikon} ukuran={W * 0.115 * skala} warna="#FFFFFF" radius={p.r(W * 0.028)}
        latar={`linear-gradient(135deg, ${p.aksen}, ${p.aksen2})`} />
    </div>
  ) : null;
  let badan = null;
  if (s.jenis === 'hook') {
    badan = (
      <>
        {lencana}
        <Judul teks={s.judul} sorot={s.sorot} F={F} p={p} lebar={lebar} maks={besar} />
        {s.sub ? <div style={{marginTop: H * 0.025}}><Paragraf teks={s.sub} ukuran={isi * 1.05} warna={p.sub} /></div> : null}
      </>
    );
  } else if (s.jenis === 'isi') {
    badan = (
      <>
        {/* Ikon menggantikan garis aksen: dua-duanya penanda pembuka yang sama. */}
        {lencana || <div style={{width: W * 0.09, height: W * 0.014, borderRadius: W * 0.007, background: p.aksen,
          marginBottom: H * 0.025}} />}
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
      {!hanyaTeks && s.gambar && s.latar ? (
        <>
          {/* Latar bersama (gambar user / foto stok): sedikit buram + lapisan warna tema, supaya
              warna teks tema tetap terbaca di slide mana pun. Kontrasnya diukur Python. */}
          <Img src={s.gambar} style={{width: W, height: H, objectFit: 'cover', filter: `blur(${W * 0.004}px)`,
            scale: '1.03'}} />
          {/* 58%: foto masih terlihat; terukur kontras teks tetap > 8 (ambang 3). 72% membuat foto nyaris hilang. */}
          <AbsoluteFill style={{background: `${p.latar}94`}} />
        </>
      ) : null}
      {!hanyaTeks && s.gambar && !s.latar ? (
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
        fontSize: W * 0.03, color: s.gambar && !s.latar && !hanyaTeks ? '#FFFFFF' : p.aksen, letterSpacing: 1}}>
        {`${i + 1}/${n}`}{platform === 'ig' && i === 0 ? '   geser →' : ''}
      </div>
      {/* Watermark: di dalam kotak aman, pojok bawah. */}
      {watermark && (
        <div style={{position: 'absolute', right: W * 0.07, bottom: H * (platform === 'ig' ? 0.045 : 0.22), 
          fontFamily: ISI, fontWeight: 600, fontSize: W * 0.035,
          color: s.gambar && !s.latar && !hanyaTeks ? 'rgba(255,255,255,0.7)' : p.sub, letterSpacing: 1}}>
          {watermark}
        </div>
      )}
    </AbsoluteFill>
  );
};

export const Carousel = ({slides, kotak, tema, hanyaTeks = false, platform = 'ig', watermark}) => {
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
    kotak={kotak[i]} hanyaTeks={hanyaTeks} platform={platform} watermark={watermark} />;
};
