"use client";

import { IconSearch } from "@tabler/icons-react";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";
import MovieCard from "@/components/MovieCard";
import PageBanner, { SEARCH, TAB, useGrowStage } from "@/components/PageBanner";
import { api, type Work } from "@/lib/api";

const PHASES = ["1", "2", "3", "4", "5", "6"];

export default function ExplorePage() {
  return (
    <Suspense>
      <Explore />
    </Suspense>
  );
}

function Explore() {
  const router = useRouter();
  const params = useSearchParams();
  const q = params.get("q") ?? "";
  const phase = params.get("phase") ?? "";
  const [works, setWorks] = useState<Work[] | null>(null);
  const [error, setError] = useState("");
  const main = useRef<HTMLElement>(null);
  useGrowStage(main, 1200);

  useEffect(() => {
    const query = new URLSearchParams({ canon: "true" });
    if (q) query.set("q", q);
    if (phase) query.set("phase", phase);
    let alive = true; // 검색어를 바꾼 뒤 늦게 온 이전 결과는 버린다
    api<Work[]>(`/works?${query}`)
      .then((w) => {
        if (!alive) return;
        setWorks(w);
        setError("");
      })
      .catch((e: Error) => alive && setError(e.message));
    return () => {
      alive = false;
    };
  }, [q, phase]);

  const go = (next: { q?: string; phase?: string }) => {
    const query = new URLSearchParams({ q, phase, ...next });
    [...query.keys()].forEach((k) => !query.get(k) && query.delete(k));
    router.replace(`/explore/${query.size ? `?${query}` : ""}`);
  };

  return (
    <>
      <div className="glow" data-depth="14" style={{ left: 700, top: 120, width: 560, height: 360, background: "#6a4fd6", opacity: 0.55 }} />
      <div className="glow" data-depth="10" style={{ left: 1360, top: 520, width: 520, height: 460, background: "#2f7fd8", opacity: 0.5 }} />
      <div className="glow" data-depth="10" style={{ left: 60, top: 640, width: 520, height: 420, background: "#c2569a", opacity: 0.4 }} />

      <PageBanner
        crumb="탐색"
        title="MCU ARCHIVE"
        desc="MCU 정식 영화 · 시리즈를 공개 연도순으로 모았어요. 카드를 누르면 줄거리와 정보를 볼 수 있어요"
        aside={
          <div className="absolute top-10 right-10 text-right">
            <b className="block text-[22px] font-bold text-hi tabular-nums">{works?.length ?? "-"}</b>
            <span className="text-[12.5px] text-low">{q || phase ? "찾은 작품" : "전체 작품"}</span>
          </div>
        }
      >
        <nav className="flex gap-1.5" aria-label="페이즈">
          {["", ...PHASES].map((p) => (
            <button key={p || "all"} type="button" aria-current={phase === p ? "page" : undefined} onClick={() => go({ phase: p })} className={TAB}>
              {p ? `PHASE ${p}` : "전체"}
            </button>
          ))}
        </nav>
        <form
          role="search"
          className={`ml-auto ${SEARCH}`}
          onSubmit={(e) => {
            e.preventDefault();
            go({ q: new FormData(e.currentTarget).get("q")?.toString().trim() ?? "" });
          }}
        >
          <IconSearch size={16} aria-hidden />
          <input key={q} name="q" defaultValue={q} placeholder="한국어 제목이나 원제" aria-label="작품 검색" className="w-full bg-transparent text-sm text-ink outline-none placeholder:text-low" />
        </form>
      </PageBanner>

      <section ref={main} className="absolute top-[474px] right-[120px] left-[120px] z-10">
        {error && <p className="text-amber">{error}</p>}
        {!works && !error && <p className="text-mid">불러오는 중…</p>}
        {works?.length === 0 && <p className="text-mid">찾는 작품이 없어요. 다른 이름으로 검색해 보세요.</p>}
        <div className="grid grid-cols-6 gap-[22px] [perspective:1200px]">
          {works?.map((w) => (
            <MovieCard key={w.work_id} work={w} className="h-[250px] w-full" />
          ))}
        </div>
      </section>
    </>
  );
}
