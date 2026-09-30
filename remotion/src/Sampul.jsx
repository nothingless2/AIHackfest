import React from 'react';
import {AbsoluteFill, Img, delayRender, continueRender, useVideoConfig} from 'remotion';
// `gambar` = data URL JPEG frame terbaik (dikirim Python lewat props): tidak ada berkas sementara
// yang perlu ditaruh di public/ lalu dibersihkan.
import {staticFile} from 'remotion';
import {loadFont} from '@remotion/fonts';
import {fitText} from '@remotion/layout-utils';

// Cover video (still 1080x1920): frame terbaik + judul besar. Frame dipilih Python dengan mengukur
// (luas wajah x ketajaman), judulnya dari kartu pembuka yang sudah divalidasi. Zona judul dihitung
// Python dari kotak wajah, jadi teks tidak pernah menutupi muka.

const FONT = {family: 'Montserrat ExtraBold', file: 'fonts/Montserrat-ExtraBold.ttf', weight: 800};
const EMAS = 'linear-gradient(180deg, #FFF4B8 0%, #F7CC55 48%, #C98E22 100%)';

export const Sampul = ({gambar, judul, emas, yJudul = 0.72}) => {
  const {width, height} = useVideoConfig();
  const [siap, setSiap] = React.useState(false);
  React.useEffect(() => {
    const h = delayRender(`memuat font ${FONT.family}`);
    loadFont({family: FONT.family, url: staticFile(FONT.file), weight: String(FONT.weight)})
      .then(() => { setSiap(true); continueRender(h); })
      .catch((e) => { throw e; });
  }, []);
  if (!siap) return null;
  const kata = (judul || '').split(/\s+/).filter(Boolean);
  const baris = kata.length > 3 ? [kata.slice(0, Math.ceil(kata.length / 2)), kata.slice(Math.ceil(kata.length / 2))] : [kata];
  const terpanjang = baris.map((b) => b.join(' ')).sort((a, b) => b.length - a.length)[0] || 'x';
  const ukuran = Math.min(height * 0.075, fitText({text: terpanjang, withinWidth: width * 0.84,
    fontFamily: FONT.family, fontWeight: String(FONT.weight)}).fontSize);
  let idx = -1;
  return (
    <AbsoluteFill style={{background: '#000'}}>
      {gambar ? <Img src={gambar} style={{width, height, objectFit: 'cover'}} /> : null}
      <AbsoluteFill style={{background:
        `linear-gradient(to bottom, rgba(0,0,0,0) ${Math.round(yJudul * 100) - 22}%, rgba(0,0,0,0.78) ${Math.round(yJudul * 100) + 6}%, rgba(0,0,0,0.9) 100%)`}} />
      {kata.length ? (
        <div style={{position: 'absolute', left: 0, width, top: height * yJudul, translate: '0 -50%',
          textAlign: 'center', fontFamily: `"${FONT.family}", sans-serif`, fontWeight: FONT.weight,
          fontSize: ukuran, lineHeight: 1.06, color: '#FFFFFF', padding: `0 ${width * 0.06}px`,
          textShadow: `0 ${ukuran * 0.05}px ${ukuran * 0.2}px rgba(0,0,0,0.8)`}}>
          {baris.map((b, i) => (
            <div key={i} style={{whiteSpace: 'pre'}}>
              {b.map((w, j) => {
                idx += 1;
                return idx === emas ? (
                  <span key={j} style={{backgroundImage: EMAS, WebkitBackgroundClip: 'text',
                    backgroundClip: 'text', color: 'transparent'}}>{(j ? ' ' : '') + w}</span>
                ) : <span key={j}>{(j ? ' ' : '') + w}</span>;
              })}
            </div>
          ))}
        </div>
      ) : null}
    </AbsoluteFill>
  );
};
