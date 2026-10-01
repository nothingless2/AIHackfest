import React from 'react';
import {AbsoluteFill, Img, useVideoConfig} from 'remotion';
import {MotionOverlay} from './MotionOverlay.jsx';
import {CaptionDinamis} from './CaptionDinamis.jsx';
import {bacaTema} from './tema.js';

// Satu kartu contoh per preset gaya (scripts/gaya.py pratinjau): kartu pembuka + pil sorot +
// caption, memakai komponen ASLI yang dipakai render -- bukan tiruan. Python mengambil still di
// frame PRATINJAU_FRAME (overlay_remotion), saat semua animasi masuk sudah diam. (<Freeze> di
// komposisi 2 frame ternyata menjepit frame-nya: kartu terekam setengah pudar.) Latar `gambar`
// sintetis dari Python: pratinjau di-cache untuk semua pengguna, jadi tidak boleh memuat bahan
// milik siapa pun.

export const PratinjauGaya = ({gambar, label, tema}) => {
  const {width, height} = useVideoConfig();
  const T = bacaTema(tema);
  const keluarga = T.font ? T.font.family : 'Montserrat ExtraBold';
  return (
    <AbsoluteFill style={{background: '#000'}}>
      {gambar ? <Img src={gambar} style={{width, height, objectFit: 'cover'}} /> : null}
      <MotionOverlay tata="atas" aksen="#8B5CF6" tema={tema} items={[
        {jenis: 'kartu_hook', teks: 'Rahasia edit lebih cepat', sorot: 'cepat', emoji: '🎬',
          mulai: 0, selesai: 4, masukFrames: 12, keluarFrames: 8},
        {jenis: 'sorot', teks: 'Gratis', emoji: '✨', mulai: 0, selesai: 4, masukFrames: 12, keluarFrames: 8},
      ]} />
      <CaptionDinamis y={0.72} lebarMaks={0.74} tema={tema} items={[
        {kata: ['ini', 'rahasianya'], kunci: 1, mulai: 0, selesai: 4, masukFrames: 8, varian: 'biasa'},
      ]} />
      <div style={{position: 'absolute', left: 0, right: 0, bottom: height * 0.04, display: 'flex',
        justifyContent: 'center'}}>
        <div style={{background: 'rgba(0,0,0,0.72)', color: '#FFFFFF', borderRadius: 999,
          padding: `${height * 0.012}px ${width * 0.07}px`, fontSize: height * 0.042,
          fontFamily: `"${keluarga}", sans-serif`, fontWeight: 800, letterSpacing: 1}}>
          {label}
        </div>
      </div>
    </AbsoluteFill>
  );
};
