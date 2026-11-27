"use client";

import { useEffect, useRef } from "react";

// 버스: 행성 고리(public/ring.svg 와 같은 타원)를 타고 왼쪽에서 오른쪽으로 계속 지나간다. 꽁무니에 엔진 빛
const RING = { cx: 1200, cy: 1400, rx: 1500, ry: 900, tilt: (-14 * Math.PI) / 180 };
const SPEED = 170; // px/s
const LIFT = 82; // 고리 선에서 버스 중심까지 (바퀴가 선 위에 닿게)
const TAIL = 168;
const cos = Math.cos(RING.tilt);
const sin = Math.sin(RING.tilt);

function point(a: number) {
  const X = RING.rx * Math.cos(a);
  const Y = RING.ry * Math.sin(a);
  return { x: RING.cx + X * cos - Y * sin, y: RING.cy + X * sin + Y * cos };
}
function tangent(a: number) {
  const dX = -RING.rx * Math.sin(a);
  const dY = RING.ry * Math.cos(a);
  return { x: dX * cos - dY * sin, y: dX * sin + dY * cos };
}

export default function HeroBus() {
  const bus = useRef<HTMLDivElement>(null);
  const trail = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const ctx = trail.current?.getContext("2d");
    const el = bus.current;
    if (!ctx || !el) return;
    const calm = matchMedia("(prefers-reduced-motion: reduce)").matches;
    let th = calm ? -1.95 : -2.5;
    let last = performance.now();
    let raf = 0;

    const frame = (now: number) => {
      const dt = Math.min(0.05, (now - last) / 1000);
      last = now;
      const tg = tangent(th);
      const len = Math.hypot(tg.x, tg.y);
      if (!calm) th += (SPEED * dt) / len;
      const p = point(th);
      if (p.x > 2150) th = -2.55; // 오른쪽으로 나가면 왼쪽 아래 화면 밖에서 다시 올라온다

      const ux = tg.x / len;
      const uy = tg.y / len; // 진행 방향
      const nx = uy;
      const ny = -ux; // 위쪽 법선
      const x = p.x + nx * LIFT;
      const y = p.y + ny * LIFT;
      el.style.transform = `translate(${x}px, ${y}px) translate(-50%, -50%) rotate(${(Math.atan2(uy, ux) * 180) / Math.PI}deg)`;

      const ex = x - ux * TAIL - nx * 20;
      const ey = y - uy * TAIL - ny * 20;
      const flick = 0.75 + Math.random() * 0.25;
      ctx.clearRect(0, 0, 1920, 1080);
      const g = ctx.createRadialGradient(ex, ey, 0, ex, ey, 60 * flick);
      g.addColorStop(0, "rgba(255,255,255,0.85)");
      g.addColorStop(0.3, "rgba(130,185,255,0.6)");
      g.addColorStop(1, "rgba(60,90,200,0)");
      ctx.fillStyle = g;
      ctx.beginPath();
      ctx.arc(ex, ey, 60 * flick, 0, Math.PI * 2);
      ctx.fill();
      if (!calm) raf = requestAnimationFrame(frame);
    };
    raf = requestAnimationFrame(frame);
    return () => cancelAnimationFrame(raf);
  }, []);

  return (
    <>
      <canvas ref={trail} width={1920} height={1080} className="pointer-events-none absolute top-0 left-0 z-[11] h-[1080px] w-[1920px] mix-blend-screen" data-depth="12" aria-hidden />
      <div ref={bus} className="absolute top-0 left-0 z-[12] h-[196px] w-[360px] will-change-transform" data-depth="12" aria-hidden>
        {/* 좌우를 뒤집어 앞이 오른쪽을 보게. 창문 너머로 로봇 조종사 · 꼬마 외계인 · 꼬마 우주비행사가 보인다 */}
        <div className="absolute inset-0 -scale-x-100 animate-[bob_1.8s_ease-in-out_infinite] drop-shadow-[10px_14px_14px_rgb(0_0_0/0.55)] saturate-[0.6] brightness-[0.92]">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src="/riders.svg" alt="" className="absolute inset-0 h-full w-full" />
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src="/bus-cabin.png" alt="" className="absolute inset-0 h-full w-full" />
        </div>
      </div>
    </>
  );
}
