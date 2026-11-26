"use client";

import { useEffect, useRef } from "react";

// 1920x1080 무대 + 별 · 성운 배경. 마우스에 따라 [data-depth] 요소는 깊이별로, .tilt 카드는 3D 로 기운다
export default function Stage({ children }: { children: React.ReactNode }) {
  const stage = useRef<HTMLDivElement>(null);
  const stars = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const fit = () =>
      document.documentElement.style.setProperty("--s", String(Math.min(innerWidth / 1920, innerHeight / 1080)));
    fit();
    addEventListener("resize", fit);

    const ctx = stars.current?.getContext("2d");
    if (ctx) {
      let seed = 7; // 매번 같은 별자리
      const rand = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
      for (let i = 0; i < 420; i++) {
        ctx.globalAlpha = 0.25 + rand() * 0.75;
        ctx.fillStyle = rand() > 0.85 ? "#bfe9ff" : "#ffffff";
        ctx.beginPath();
        ctx.arc(rand() * 1920, rand() * 1080, rand() ** 3 * 1.8 + 0.3, 0, Math.PI * 2);
        ctx.fill();
      }
    }

    const el = stage.current!;
    const move = (e: PointerEvent) => {
      const r = el.getBoundingClientRect();
      const x = (e.clientX - r.left) / r.width - 0.5;
      const y = (e.clientY - r.top) / r.height - 0.5;
      el.querySelectorAll<HTMLElement>("[data-depth]").forEach((d) => {
        const depth = Number(d.dataset.depth);
        d.style.translate = `${-x * depth}px ${-y * depth}px`;
      });
      const card = (e.target as Element).closest<HTMLElement>(".tilt");
      if (card) {
        const c = card.getBoundingClientRect();
        const cx = (e.clientX - c.left) / c.width - 0.5;
        const cy = (e.clientY - c.top) / c.height - 0.5;
        card.style.setProperty("--rx", `${-cy * 10}deg`);
        card.style.setProperty("--ry", `${cx * 12}deg`);
        card.style.setProperty("--mx", `${(cx + 0.5) * 100}%`);
        card.style.setProperty("--my", `${(cy + 0.5) * 100}%`);
      }
    };
    el.addEventListener("pointermove", move);
    return () => {
      removeEventListener("resize", fit);
      el.removeEventListener("pointermove", move);
    };
  }, []);

  return (
    <div className="viewport">
      <div ref={stage} className="stage">
        <div className="space" />
        <canvas ref={stars} width={1920} height={1080} className="absolute inset-0" data-depth="6" />
        {children}
      </div>
    </div>
  );
}
