export function attachCardTilt(root: ParentNode = document): () => void {
  const cards = Array.from(root.querySelectorAll<HTMLElement>('[data-tilt]'));
  const disposers = cards.map((card) => {
    const handleMove = (event: PointerEvent) => {
      const rect = card.getBoundingClientRect();
      const x = (event.clientX - rect.left) / rect.width - 0.5;
      const y = (event.clientY - rect.top) / rect.height - 0.5;
      const mobileOffset = window.innerWidth < 980 ? ' translateX(-50%)' : '';
      card.style.transform = `rotateX(${-y * 10}deg) rotateY(${x * 14}deg) translateY(-8px)${mobileOffset}`;
    };
    const handleLeave = () => {
      card.style.transform = '';
    };
    card.addEventListener('pointermove', handleMove);
    card.addEventListener('pointerleave', handleLeave);
    return () => {
      card.removeEventListener('pointermove', handleMove);
      card.removeEventListener('pointerleave', handleLeave);
    };
  });

  return () => disposers.forEach((dispose) => dispose());
}
