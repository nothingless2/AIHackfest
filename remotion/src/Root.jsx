import React from 'react';
import {Composition} from 'remotion';
import {TextOverlay} from './TextOverlay.jsx';

export const Root = () => (
  <Composition
    id="TextOverlay"
    component={TextOverlay}
    width={1080}
    height={1920}
    fps={24}
    durationInFrames={48}
    defaultProps={{items: [{text: 'Contoh teks', mulai: 0, selesai: 2}], posisi: 'tengah', font: 'santai', animasi: 'pop'}}
    calculateMetadata={({props}) => ({
      durationInFrames: Math.max(2, Math.ceil((props.durasi || 2) * (props.fps || 24))),
      width: props.lebar || 1080, height: props.tinggi || 1920, fps: props.fps || 24,
    })}
  />
);
