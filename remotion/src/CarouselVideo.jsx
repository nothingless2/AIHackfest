import React from 'react';
import {Sequence, useVideoConfig, spring, useCurrentFrame, AbsoluteFill} from 'remotion';
import {Carousel} from './Carousel.jsx';

export const CarouselVideo = ({slides, kotak, tema, platform = 'ig', watermark}) => {
  const {fps} = useVideoConfig();
  // 3 detik per slide
  const DURASI_SLIDE = 3 * fps;

  return (
    <AbsoluteFill style={{backgroundColor: '#000'}}>
      {slides.map((_, i) => {
        return (
          <Sequence key={i} from={i * DURASI_SLIDE} durationInFrames={DURASI_SLIDE}>
            <SlideAnimasi
              slideIndex={i}
              slides={slides}
              kotak={kotak}
              tema={tema}
              platform={platform}
              watermark={watermark}
            />
          </Sequence>
        );
      })}
    </AbsoluteFill>
  );
};

const SlideAnimasi = ({slideIndex, ...props}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  
  // Efek slide-in dari bawah dan fade-in
  const masuk = spring({
    frame,
    fps,
    config: {damping: 12, stiffness: 90, mass: 0.5},
  });
  
  const opacity = spring({
    frame,
    fps,
    config: {damping: 100, stiffness: 100},
  });

  return (
    <AbsoluteFill style={{
      opacity,
      transform: `translateY(${(1 - masuk) * 50}px)`,
    }}>
      <Carousel slideIndex={slideIndex} {...props} />
    </AbsoluteFill>
  );
};
