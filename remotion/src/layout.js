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
export function susunBaris({text, lebar, fontFamily, fontWeight, maks, upper}) {
  const words = (upper ? text.toUpperCase() : text).split(/\s+/).filter(Boolean);
  let terbaik = null;
  for (let n = 1; n <= Math.min(3, words.length); n++) {
    const baris = bagi(words, n);
    const terpanjang = baris.map((b) => b.join(' ')).sort((a, b) => b.length - a.length)[0];
    const ukuran = Math.min(
      maks,
      fitText({text: terpanjang, withinWidth: lebar, fontFamily, fontWeight}).fontSize,
    );
    if (!terbaik || ukuran > terbaik.ukuran * 1.08) terbaik = {baris, ukuran};
  }
  return terbaik;
}
