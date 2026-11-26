"use client";

import { IconChevronRight, IconMessageCircle, IconRocket } from "@tabler/icons-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { useChat } from "@/components/ChatPanel";
import HeroBus from "@/components/HeroBus";
import { api, formatKo, workHref, type Work } from "@/lib/api";

// "지금 인기 있는 영화": 데이터에 인기 값이 없어서 팀이 고른 목록 (원제 → 카드 그림)
const FEATURED: [string, string][] = [
  ["Avengers: Endgame", "/art-endgame.svg"],
  ["Spider-Man: No Way Home", "/art-no-way-home.svg"],
  ["Guardians of the Galaxy", "/art-guardians.svg"],
  ["Doctor Strange", "/art-doctor-strange.svg"],
];

const css = (style: Record<string, string | number>) => style as React.CSSProperties;

export default function Home() {
  const chat = useChat();
  const [works, setWorks] = useState<Work[] | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api<Work[]>("/works?canon=true")
      .then(setWorks)
      .catch((e: Error) => setError(e.message));
  }, []);

  const featured = FEATURED.flatMap(([title, art]) => {
    const w = works?.find((x) => x.title_original === title);
    return w ? [{ ...w, art }] : [];
  });

  return (
    <>
      <div className="orb" data-depth="30" style={css({ left: 60, top: 150, width: 54, height: 54, "--t": "8s" })} />
      <div className="orb" data-depth="18" style={css({ left: 1200, top: 150, width: 40, height: 40, "--a": "40deg", "--t": "11s" })} />
      <div className="orb ringed" data-depth="24" style={css({ left: 1780, top: 150, width: 90, height: 90, "--a": "120deg" })} />

      {/* 지평선 행성: 중심 (1200, 1400). 코스믹 블루 바탕에 골드 · 딥 레드 띠, 왼쪽 위에서 빛을 받는다 */}
      <div
        className="absolute top-[600px] left-[50px] z-[3] h-[1600px] w-[2300px] overflow-hidden rounded-full saturate-[0.85] shadow-[inset_0_3px_0_rgb(200_222_255/0.5),0_-10px_40px_rgb(80_150_220/0.22),0_-40px_120px_rgb(214_168_92/0.07)]"
        style={{
          background:
            "radial-gradient(ellipse 30% 7% at 30% 3%, rgb(70 140 210 / 0.42), transparent 70%), radial-gradient(ellipse 26% 6% at 52% 1.5%, rgb(70 165 190 / 0.28), transparent 70%), radial-gradient(ellipse 20% 5% at 70% 3.5%, rgb(214 168 92 / 0.3), transparent 70%), radial-gradient(ellipse 16% 4% at 42% 7%, rgb(150 40 40 / 0.22), transparent 70%), radial-gradient(ellipse at 38% 0%, #1d2746 0%, #10162b 16%, #0a0d18 36%, #06070c 66%)",
        }}
        data-depth="8"
        aria-hidden
      >
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src="/planet-surface.svg" alt="" className="absolute inset-0 h-full w-full" />
      </div>
      {/* 빛나는 고리: 행성과 같은 중심, -14도 기울임. 버스가 이 선을 타고 달린다 */}
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src="/ring.svg" alt="" className="pointer-events-none absolute top-0 left-0 z-[5] h-[1080px] w-[1920px]" data-depth="8" aria-hidden />
      <HeroBus />

      {/* 그루트: 오른쪽 행성 테두리에 걸터앉아 다리를 늘어뜨린다. 누르면 로어 AI 가 열린다 */}
      <button
        type="button"
        onClick={() => chat.open()}
        className="group absolute top-[263px] left-[1292px] z-[14] h-[420px] w-[216px] cursor-pointer before:absolute before:top-[calc(84.5%-8px)] before:right-[12%] before:left-[12%] before:-z-10 before:h-4 before:rounded-full before:bg-[radial-gradient(closest-side,rgb(0_0_0/0.75),transparent)] before:blur-[3px] before:content-[''] after:pointer-events-none after:absolute after:top-[calc(84.5%-3px)] after:-right-[6%] after:-left-[6%] after:h-1.5 after:rounded-full after:bg-[radial-gradient(closest-side,rgb(200_225_255/0.55),rgb(130_185_255/0.18)_60%,transparent)] after:blur-[2px] after:content-['']"
        data-depth="8"
        aria-label="로어 AI 그루트에게 물어보기"
      >
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src="/groot.png" alt="" className="absolute inset-0 h-full w-full origin-[50%_84.5%] animate-[kick_1.2s_ease-in-out_infinite_alternate] drop-shadow-[6px_8px_8px_rgb(0_0_0/0.5)] saturate-[0.8] brightness-[0.62] [clip-path:inset(84.5%_0_0_0)]" />
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src="/groot.png" alt="" className="absolute inset-0 h-full w-full origin-[50%_84.5%] animate-[sway_5s_ease-in-out_infinite] drop-shadow-[3px_0_1.5px_rgb(120_210_235/0.45)] saturate-[0.85] brightness-[0.95] [clip-path:inset(0_0_15.5%_0)] group-hover:[animation-duration:1.6s]" />
      </button>
      <button
        type="button"
        onClick={() => chat.open()}
        className="absolute top-[640px] left-[1500px] z-[15] flex items-center gap-2 rounded-[10px] border border-white/12 bg-[rgb(14_17_28/0.86)] px-3.5 py-[9px] text-[13.5px] font-semibold whitespace-nowrap text-[#e8ebf4] shadow-[0_10px_24px_rgb(0_0_0/0.45)] backdrop-blur-[10px] transition before:size-1.5 before:rounded-full before:bg-cyan before:shadow-[0_0_0_3px_rgb(143_212_255/0.18)] before:content-[''] hover:-translate-y-0.5 hover:border-violet/50"
      >
        궁금한 거 물어봐 <small className="text-xs font-normal text-[#8f9bb8]">누르면 로어 AI가 열려요</small>
      </button>

      <section className="absolute top-[204px] left-[120px] z-[16] w-[600px]">
        <div className="eyebrow">MARVEL CINEMATIC UNIVERSE · LORE GUIDE</div>
        <h1 className="mt-[22px] text-[62px] leading-[1.16] font-bold tracking-[-0.025em] text-ink">
          MCU의 모든 순간,
          <br />
          <span className="grad">무엇이든 물어보세요</span>
        </h1>
        <p className="mt-5 mb-[34px] w-[500px] text-lg leading-[1.65] text-muted">
          영화와 시리즈의 줄거리 · 인물 · 세계관을 위키 원문에서 찾아 출처와 함께 보여드려요.
        </p>
        <div className="flex gap-3">
          <Link href="/explore/" className="btn btn-primary">
            <IconRocket size={20} aria-hidden />
            탐험 시작하기
          </Link>
          <button type="button" className="btn" onClick={() => chat.open()}>
            <IconMessageCircle size={20} aria-hidden />
            로어 AI에게 묻기
          </button>
        </div>
      </section>

      <section className="absolute top-[1010px] right-[120px] left-[120px] z-[15]">
        <div className="flex items-center text-2xl font-bold">
          지금 인기 있는 영화
          <Link href="/explore/" className="ml-auto flex items-center text-sm font-medium text-dim hover:text-ink">
            전체 보기 <IconChevronRight size={16} aria-hidden />
          </Link>
        </div>
        <div className="mt-6 grid grid-cols-4 gap-7 [perspective:1200px]">
          {featured.map((w, i) => (
            <Link
              key={w.work_id}
              href={workHref(w)}
              className="tilt block h-[290px] overflow-hidden rounded-[18px] border border-white/10 shadow-[0_24px_60px_rgb(0_0_0/0.6)] focus-visible:outline-2 focus-visible:outline-[#9fc2ff]"
            >
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={w.art} alt="" className="absolute inset-0 h-full w-full object-cover" />
              <div className="grain" />
              <div className="absolute inset-0 bg-[linear-gradient(180deg,rgb(7_8_13/0.35)_0%,transparent_28%,transparent_48%,rgb(7_8_13/0.95)_100%)]" />
              {w.phase && (
                <span className="absolute top-5 left-[22px] rounded-md border border-white/14 bg-[rgb(7_8_13/0.55)] px-2 py-[5px] font-mono text-[11px] tracking-[0.14em] text-[#c9cee0] backdrop-blur-[8px]">
                  PHASE {w.phase}
                </span>
              )}
              <span className="absolute top-2.5 right-[22px] text-[40px] leading-[1.2] font-semibold text-white/80 tabular-nums [text-shadow:0_2px_12px_rgb(0_0_0/0.6)]">
                {i + 1}
              </span>
              <div className="absolute right-[22px] bottom-5 left-[22px]">
                <b className="block text-[22px] font-bold tracking-tight">{w.title_ko}</b>
                <span className="mt-1.5 block font-mono text-[12.5px] tracking-[0.04em] text-[#9aa1b8]">
                  {[w.year, formatKo(w.format)].filter(Boolean).join(" · ")}
                </span>
              </div>
            </Link>
          ))}
          {!works && !error && <p className="text-muted">작품을 불러오는 중…</p>}
          {error && <p className="text-amber">{error}</p>}
        </div>
      </section>
    </>
  );
}
