import React from 'react';
import {Composition} from 'remotion';
import {TextOverlay} from './TextOverlay.jsx';
import {MotionOverlay} from './MotionOverlay.jsx';
import {CaptionDinamis} from './CaptionDinamis.jsx';

const metadata = ({props}) => ({
  durationInFrames: Math.max(2, Math.ceil((props.durasi || 2) * (props.fps || 24))),
  width: props.lebar || 1080, height: props.tinggi || 1920, fps: props.fps || 24,
});

export const Root = () => (
  <>
    <Composition
      id="TextOverlay"
      component={TextOverlay}
      width={1080}
      height={1920}
      fps={24}
      durationInFrames={48}
      defaultProps={{items: [{text: 'Contoh teks', mulai: 0, selesai: 2}], posisi: 'tengah', font: 'santai', animasi: 'pop'}}
      calculateMetadata={metadata}
    />
    <Composition
      id="MotionOverlay"
      component={MotionOverlay}
      width={1080}
      height={1920}
      fps={24}
      durationInFrames={48}
      defaultProps={{items: [{jenis: 'sorot', teks: 'Contoh', mulai: 0, selesai: 2}], aksen: '#8B5CF6'}}
      calculateMetadata={metadata}
    />
    <Composition
      id="CaptionDinamis"
      component={CaptionDinamis}
      width={1080}
      height={1920}
      fps={24}
      durationInFrames={48}
      defaultProps={{items: [{kata: ['dan', 'Remotion'], kunci: 1, mulai: 0, selesai: 2, masukFrames: 8}]}}
      calculateMetadata={metadata}
    />
  </>
);
