"use client";

import { IconSearch } from "@tabler/icons-react";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import MovieCard from "@/components/MovieCard";
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
    <div className="absolute top-[150px] right-[120px] bottom-0 left-[120px] z-10 flex flex-col">
      <header className="flex items-end">
        <div>
          <h1 className="text-[56px] font-extrabold tracking-tight">
            <span className="grad">탐색</span>
          </h1>
          <p className="mt-2.5 text-lg text-muted">MCU 정식 영화 · 시리즈를 공개 연도순으로 모았어요</p>
        </div>
        <form
          role="search"
          className="ml-auto flex h-[52px] w-[420px] items-center gap-2.5 rounded-full border border-line-hi bg-white/5 px-5 focus-within:border-cyan"
          onSubmit={(e) => {
            e.preventDefault();
            go({ q: new FormData(e.currentTarget).get("q")?.toString().trim() ?? "" });
          }}
        >
          <IconSearch size={18} className="text-dim" aria-hidden />
          <input key={q} name="q" defaultValue={q} placeholder="한국어 제목이나 원제" aria-label="작품 검색" className="w-full bg-transparent outline-none placeholder:text-dim" />
        </form>
      </header>

      <div className="mt-8 flex gap-2" role="group" aria-label="페이즈">
        {["", ...PHASES].map((p) => (
          <button key={p || "all"} type="button" aria-pressed={phase === p} onClick={() => go({ phase: p })} className={`chip h-9 px-4 text-sm ${phase === p ? "chip-on" : "hover:bg-white/10"}`}>
            {p ? `PHASE ${p}` : "전체"}
          </button>
        ))}
        {works && <span className="ml-auto self-center text-sm text-dim">{works.length}편</span>}
      </div>

      <div className="scroll-area mt-6 -mr-4 flex-1 pr-4 pb-10">
        {error && <p className="text-amber">{error}</p>}
        {!works && !error && <p className="text-muted">불러오는 중…</p>}
        {works?.length === 0 && <p className="text-muted">찾는 작품이 없어요. 다른 이름으로 검색해 보세요.</p>}
        <div className="grid grid-cols-6 gap-[22px] pt-3 [perspective:1200px]">
          {works?.map((w) => (
            <MovieCard key={w.work_id} work={w} className="h-[250px] w-full" />
          ))}
        </div>
      </div>
    </div>
  );
}
