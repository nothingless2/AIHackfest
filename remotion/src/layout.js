import {fitText} from '@remotion/layout-utils';

// Bagi kata ke n baris dengan jumlah karakter seimbang.
function bagi(words, n) {
  if (n <= 1 || words.length <= 1) return [words];
  const total = words.join(' ').length;
  const target = total / n;
  const baris = [[]];
  let panjang = 0;
  for (const w of words) {
    const cur = baris[baris.length - 1];
    if (cur.length && panjang + w.length / 2 > target * baris.length && baris.length < n) {
      baris.push([]);
    }
    baris[baris.length - 1].push(w);
    panjang += w.length + 1;
  }
  return baris.filter((b) => b.length);
}

// Pilih jumlah baris (1-3) yang memberi huruf terbesar tapi TIDAK melebihi `maks` piksel.
// Diukur dengan font sungguhan (fitText), bukan diperkirakan dari jumlah karakter.
// ukurSemua: ukur SETIAP baris, bukan hanya yang karakternya terbanyak. Dua baris sama panjang
// karakternya bisa berbeda lebar ("Perintah AI yang" vs "Langsung Kepakai", 3 Okt: baris kedua
// terpotong di tepi slide). Opsional supaya pemanggil lama (overlay video) tetap identik piksel.
export function susunBaris({text, lebar, fontFamily, fontWeight, maks, upper, ukurSemua = false}) {
  const words = (upper ? text.toUpperCase() : text).split(/\s+/).filter(Boolean);
  let terbaik = null;
  for (let n = 1; n <= Math.min(3, words.length); n++) {
    const baris = bagi(words, n);
    const terpanjang = baris.map((b) => b.join(' ')).sort((a, b) => b.length - a.length)[0];
    const diukur = ukurSemua ? baris.map((b) => b.join(' ')) : [terpanjang];
    const ukuran = Math.min(
      maks,
      ...diukur.map((t) => fitText({text: t, withinWidth: lebar, fontFamily, fontWeight}).fontSize),
    );
    if (!terbaik || ukuran > terbaik.ukuran * 1.08) terbaik = {baris, ukuran};
  }
  return terbaik;
}
