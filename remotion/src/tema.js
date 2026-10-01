import {staticFile} from 'remotion';
import {loadFont} from '@remotion/fonts';
import {FONTS} from './fonts.js';

// Tema dari preset gaya (config/gaya/*.json), divalidasi scripts/gaya.py sebelum sampai sini:
// warna hanya hex, font hanya nama dari daftar tertutup FONTS, angka dalam rentang.
// Token yang TIDAK disebut preset bernilai undefined -> tiap komponen memakai literal bawaannya
// sendiri (`T.w.aksen ?? '#8B5CF6'`). Karena itu preset "klasik" (tema kosong) identik piksel
// dengan tampilan sebelum sistem gaya ada (tests/test_gaya.py membandingkannya dengan acuan).
export const bacaTema = (tema) => {
  const t = tema || {};
  const judul = t.font && t.font.judul;
  return {
    w: t.warna || {},
    font: judul && FONTS[judul] ? FONTS[judul] : null,
    sudut: typeof t.sudut === 'number' ? t.sudut : 1,
    cahaya: t.cahaya !== false,
    gerak: t.gerak || 'pegas',
  };
};

// Gradien teks dari 1-3 warna. Tiga warna memakai titik 0/48/100% -- sama dengan gradien emas
// bawaan, jadi preset yang hanya mengganti warnanya tetap berbentuk sama.
export const gradien = (stop) => {
  const s = stop.length === 1 ? [stop[0], stop[0]] : stop;
  const titik = s.length === 3 ? ['0%', '48%', '100%'] : ['0%', '100%'];
  return `linear-gradient(180deg, ${s.map((c, i) => `${c} ${titik[i]}`).join(', ')})`;
};

// Karakter gerak: 'pegas' = konfigurasi asli tiap komponen (tidak diubah); 'halus' = redaman
// besar, tanpa memantul; 'tegas' = lebih kaku dan cepat. Spring yang memakai durationInFrames
// tetap selesai tepat di masukFrames, jadi gambar diam setelahnya tidak melompat.
export const pegas = (dasar, gerak) => {
  if (gerak === 'halus') return {...dasar, damping: dasar.damping * 2.2};
  if (gerak === 'tegas') return {...dasar, stiffness: dasar.stiffness * 1.7, damping: dasar.damping * 1.15};
  return dasar;
};

// Font judul: font tema bila ada, selain itu font bawaan komponen. Font sistem tidak dimuat.
export const muatFont = (F) => (F.file
  ? loadFont({family: F.family, url: staticFile(F.file), weight: String(F.weight)})
  : Promise.resolve());
