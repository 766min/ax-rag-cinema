"use client";

import { IconChevronRight, IconHome } from "@tabler/icons-react";
import Link from "next/link";
import { type RefObject, useEffect } from "react";

// 배너 아래 띠의 탭 버튼 · 검색칸 (커뮤니티 · 탐색 · 세계관이 같이 쓴다)
export const TAB =
  "h-9 rounded-[10px] px-4 text-sm font-medium text-mid transition-colors hover:bg-white/5 hover:text-hi aria-[current=page]:bg-hi aria-[current=page]:font-semibold aria-[current=page]:text-[#111]";
export const SEARCH =
  "flex h-[42px] w-[260px] items-center gap-2 rounded-xl border border-white/10 bg-white/4 px-3.5 text-low focus-within:border-[#9fc2ff]";

// 내용이 길어지면 무대(--h)를 아래로 늘린다 (무대 밖은 잘리므로)
export function useGrowStage(ref: RefObject<HTMLElement | null>, min: number): void {
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const grow = () =>
      document.documentElement.style.setProperty("--h", `${Math.max(min, el.offsetTop + el.offsetHeight + 120)}px`);
    const obs = new ResizeObserver(grow);
    obs.observe(el);
    // 창 크기가 바뀌거나 챗봇을 열면 fitStage 가 높이를 기본값으로 되돌리므로, 그 다음 프레임에 다시 늘린다
    const later = () => requestAnimationFrame(grow);
    addEventListener("resize", later);
    return () => {
      obs.disconnect();
      removeEventListener("resize", later);
    };
  }, [ref, min]);
}

// 페이지 머리 배너: 경로 · 제목 · 설명 + 오른쪽 위(aside) + 아래 띠(children)를 유리 덩어리 하나에
export default function PageBanner({
  crumb,
  title,
  desc,
  aside,
  children,
}: {
  crumb: string;
  title: string;
  desc: string;
  aside?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <header className="frost absolute top-[150px] right-[120px] left-[120px] z-10 overflow-hidden rounded-[26px] px-10 pt-[34px]">
      <div className="pointer-events-none absolute -top-[140px] -right-[60px] size-[560px] rounded-full bg-[radial-gradient(circle_at_35%_30%,rgb(184_164_246/0.55),rgb(111_178_255/0.25)_45%,transparent_70%)] opacity-70 blur-[2px] after:absolute after:inset-x-[-14%] after:inset-y-[38%] after:-rotate-[14deg] after:rounded-[50%] after:border-[1.5px] after:border-white/28 after:content-['']" aria-hidden />
      <nav className="relative flex items-center gap-2 text-[13px] text-low" aria-label="현재 위치">
        <Link href="/" className="flex items-center gap-1 hover:text-hi">
          <IconHome size={14} aria-hidden /> 홈
        </Link>
        <IconChevronRight size={14} aria-hidden />
        <b className="font-semibold text-mid">{crumb}</b>
      </nav>
      <h1 className="relative mt-[18px] font-display text-[52px] leading-none tracking-[0.05em] text-hi">{title}</h1>
      <p className="relative mt-3.5 text-base text-mid">{desc}</p>
      {aside}
      <div className="relative -mx-10 mt-[30px] flex items-center gap-3 border-t border-white/8 bg-black/18 px-10 py-3.5">{children}</div>
    </header>
  );
}
