import gsap from 'gsap';
import { ScrollTrigger } from 'gsap/ScrollTrigger';
import Lenis from 'lenis';
import type { QualityProfile } from '../app/qualityProfile';

export function initScrollMotion(quality: QualityProfile): () => void {
  gsap.registerPlugin(ScrollTrigger);
  const triggers: ScrollTrigger[] = [];

  gsap.utils.toArray<HTMLElement>('.reveal').forEach((element) => {
    const trigger = ScrollTrigger.create({
      trigger: element,
      start: 'top 88%',
      once: true,
      onEnter: () => element.classList.add('is-visible')
    });
    triggers.push(trigger);
  });

  let lenis: Lenis | undefined;
  let frame = 0;
  if (!quality.reducedMotion) {
    lenis = new Lenis({
      anchors: true,
      lerp: quality.name === 'ultra' ? 0.08 : 0.11,
      smoothWheel: true,
      gestureOrientation: 'both'
    });

    const raf = (time: number) => {
      lenis?.raf(time);
      frame = requestAnimationFrame(raf);
    };
    frame = requestAnimationFrame(raf);
  }

  return () => {
    triggers.forEach((trigger) => trigger.kill());
    if (frame) cancelAnimationFrame(frame);
    lenis?.destroy();
  };
}
