"use client";

import { IconChevronRight, IconPlanet, IconRocket, IconSparkles } from "@tabler/icons-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { useChat } from "@/components/ChatPanel";
import MovieCard from "@/components/MovieCard";
import { api, type Work } from "@/lib/api";

// "지금 인기 있는 영화": 데이터에 인기 값이 없어서 팀이 고른 목록 (원제로 찾는다)
const FEATURED = [
  "Avengers: Endgame",
  "Spider-Man: No Way Home",
  "Guardians of the Galaxy",
  "Doctor Strange",
  "WandaVision",
  "Thor",
];

const orb = (style: Record<string, string | number>) => style as React.CSSProperties;

export default function Home() {
  const chat = useChat();
  const [works, setWorks] = useState<Work[] | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api<Work[]>("/works?canon=true")
      .then(setWorks)
      .catch((e: Error) => setError(e.message));
  }, []);

  const featured = FEATURED.map((t) => works?.find((w) => w.title_original === t)).filter((w): w is Work => !!w);

  return (
    <>
      <div className="orb" data-depth="30" style={orb({ left: 56, top: 150, width: 60, height: 60, "--t": "8s", "--glow": "rgb(232 121 249/.6)" })} />
      <div className="orb" data-depth="18" style={orb({ left: 980, top: 150, width: 46, height: 46, "--a": "40deg", "--t": "11s" })} />
      <div className="orb ringed" data-depth="24" style={orb({ left: 1730, top: 430, width: 120, height: 120, "--a": "120deg", "--glow": "rgb(94 231 255/.55)" })} />
      <div className="orb" data-depth="14" style={orb({ left: 1000, top: 560, width: 80, height: 80, "--a": "300deg", "--t": "10s" })} />
      <div className="orb blur-near" data-depth="60" style={orb({ left: -90, top: 420, width: 220, height: 220, "--c1": "#ff7ab6", "--c2": "#ffb86b", "--c3": "#8b5cf6", "--c4": "#5ee7ff", "--glow": "rgb(255 122 182/.5)" })} />

      {/* 포털: 도는 빛 고리 + 안쪽 심연 */}
      <div className="absolute top-[110px] left-[1060px] z-[2] size-[620px]" data-depth="12" aria-hidden>
        <div className="absolute inset-[10px] animate-[spin_20s_linear_infinite_reverse] rounded-full bg-[conic-gradient(from_0deg,#5ee7ff,#fff,#ffb86b,#ff7ab6,#8b5cf6,#5ee7ff_60%,#fff,#5ee7ff)] opacity-90 blur-[28px] [mask:radial-gradient(closest-side,transparent_80%,#000_84%,#000_92%,transparent_99%)]" />
        <div className="absolute -inset-10 animate-[spin_40s_linear_infinite] rounded-full bg-[repeating-conic-gradient(from_10deg,rgb(255_220_160/0.55)_0_0.4deg,transparent_0.4deg_7deg)] opacity-70 [mask:radial-gradient(closest-side,transparent_62%,#000_70%,transparent_100%)]" />
        <div className="absolute inset-10 animate-[spin_14s_linear_infinite] rounded-full bg-[conic-gradient(from_0deg,#5ee7ff,#fff,#ffb86b,#ff7ab6,#8b5cf6,#5ee7ff_60%,#fff,#5ee7ff)] [mask:radial-gradient(closest-side,transparent_80%,#000_84%,#000_92%,transparent_99%)]" />
        <div className="absolute inset-[70px] animate-[spin_30s_linear_infinite] rounded-full bg-[radial-gradient(circle_at_50%_50%,rgb(255_240_220/0.9)_0_1.5%,rgb(255_184_107/0.35)_5%,transparent_14%),conic-gradient(from_30deg,rgb(94_231_255/0.35),transparent_20%,rgb(232_121_249/0.35)_40%,transparent_60%,rgb(139_92_246/0.4)_80%,rgb(94_231_255/0.35)),radial-gradient(circle_at_50%_50%,#0a0d2e_0_40%,#1a1a6b_62%,#4b2bb8_74%,transparent_76%)] shadow-[0_0_120px_rgb(94_231_255/0.35),inset_0_0_90px_rgb(0_0_0/0.85)]" />
      </div>
      {/* 지평선 행성 */}
      <div
        className="absolute top-[560px] left-[380px] z-[3] h-[1400px] w-[2000px] rounded-full bg-[radial-gradient(ellipse_at_50%_0%,#1a1540_0%,#07071a_40%,#04050c_70%)] shadow-[0_-2px_0_rgb(150_230_255/0.6),0_-20px_80px_rgb(94_231_255/0.35),0_-60px_160px_rgb(139_92_246/0.3)]"
        data-depth="8"
        aria-hidden
      />
      <div className="absolute top-[140px] left-[1430px] z-[12] h-[175px] w-[300px] -rotate-8" data-depth="40" aria-hidden>
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src="/bus.svg" alt="" className="w-full animate-[float_7s_ease-in-out_infinite] drop-shadow-[0_30px_30px_rgb(0_0_0/0.7)]" />
      </div>

      <section className="absolute top-[200px] left-[120px] z-10 w-[760px]" data-depth="-6">
        <div className="chip mb-[26px]">
          <IconPlanet size={14} aria-hidden />
          MARVEL CINEMATIC UNIVERSE · 영화 · 시리즈 로어
        </div>
        <h1 className="text-[84px] leading-[1.12] font-extrabold tracking-tight [text-shadow:0_8px_40px_rgb(0_0_0/0.6)]">
          당신의 영화 우주로,
          <br />
          <span className="grad">시네버스</span>를 타고
        </h1>
        <p className="mt-[26px] mb-10 w-[700px] text-[21px] leading-relaxed text-muted">
          영화와 시리즈의 줄거리 · 인물 · 세계관을 위키 원문 근거와 함께 찾아드려요.
        </p>
        <div className="flex gap-4">
          <Link href="/explore/" className="btn btn-primary">
            <IconRocket size={20} aria-hidden />
            탐험 시작하기
          </Link>
          <button type="button" className="btn" onClick={() => chat.open()}>
            <IconSparkles size={20} aria-hidden />
            로어 AI에게 묻기
          </button>
        </div>
      </section>

      <section className="absolute top-[690px] right-0 left-[120px] z-[15]">
        <div className="flex w-[1680px] items-center text-[26px] font-bold">
          지금 인기 있는 영화
          <Link href="/explore/" className="ml-auto flex items-center text-[15px] font-medium text-dim hover:text-ink">
            전체 보기 <IconChevronRight size={16} aria-hidden />
          </Link>
        </div>
        <div className="mt-[22px] flex gap-[26px] [perspective:1200px]">
          {featured.map((w, i) => (
            <MovieCard key={w.work_id} work={w} rank={i + 1} />
          ))}
          {!works && !error && <p className="text-muted">작품을 불러오는 중…</p>}
          {error && <p className="text-amber">{error}</p>}
        </div>
      </section>
    </>
  );
}
