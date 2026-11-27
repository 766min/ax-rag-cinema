"use client";

import { IconExternalLink, IconSparkles } from "@tabler/icons-react";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";
import { useChat } from "@/components/ChatPanel";
import { artOf } from "@/components/MovieCard";
import PageBanner, { useGrowStage } from "@/components/PageBanner";
import { api, formatKo, type WorkDetail } from "@/lib/api";

export default function MoviePage() {
  return (
    <Suspense>
      <Movie />
    </Suspense>
  );
}

// 작품 정보만 보여 준다 (영상 재생은 없다)
function Movie() {
  const id = useSearchParams().get("id") ?? "";
  const chat = useChat();
  const [work, setWork] = useState<WorkDetail | null>(null);
  const [error, setError] = useState("");
  const main = useRef<HTMLDivElement>(null);
  useGrowStage(main, 1200);

  useEffect(() => {
    if (!id) return;
    let alive = true; // 다른 작품으로 옮긴 뒤 늦게 온 응답은 버린다
    api<WorkDetail>(`/works/${encodeURIComponent(id)}`)
      .then((w) => {
        if (!alive) return;
        setWork(w);
        setError("");
      })
      .catch((e: Error) => alive && setError(e.message));
    return () => {
      alive = false;
    };
  }, [id]);

  const ready = work && work.work_id === id;
  return (
    <>
      <div className="glow" data-depth="14" style={{ left: 700, top: 120, width: 560, height: 360, background: "#6a4fd6", opacity: 0.55 }} />
      <div className="glow" data-depth="10" style={{ left: 1360, top: 560, width: 520, height: 460, background: "#2f7fd8", opacity: 0.45 }} />

      <div ref={main} className="absolute top-[150px] right-[120px] left-[120px] z-10">
        {(error || !id) && <p className="text-amber">{error || "작품이 지정되지 않았어요."}</p>}
        {!ready && !error && id && <p className="text-mid">불러오는 중…</p>}
        {ready && (
          <>
            <PageBanner
              className="relative"
              parents={[{ href: "/explore/", label: "탐색" }]}
              crumb={work.title_ko}
              title={work.title_original}
              desc={work.title_ko}
              aside={
                <div className="absolute top-10 right-10 flex gap-7 text-right">
                  {[
                    [work.year ?? "미정", "공개"],
                    [formatKo(work.format), "형식"],
                    [work.phase ? `PHASE ${work.phase}` : "-", "페이즈"],
                    [work.timeline_order ?? "-", "작중 시간순"],
                  ].map(([v, k]) => (
                    <div key={k}>
                      <b className="block text-[22px] font-bold text-hi tabular-nums">{v}</b>
                      <span className="text-[12.5px] text-low">{k}</span>
                    </div>
                  ))}
                </div>
              }
            >
              <button type="button" className="btn btn-primary h-[42px] text-sm" onClick={() => chat.open({ id: work.work_id, title: work.title_ko })}>
                <IconSparkles size={16} aria-hidden />이 작품에 대해 로어 AI에게 묻기
              </button>
              <span className="ml-auto text-[13px] text-low">시네버스는 작품 정보만 보여 줘요 (영상 재생은 없어요)</span>
            </PageBanner>

            <div className="mt-6 flex items-start gap-6">
              <div className={`tilt relative h-[560px] w-[420px] flex-none overflow-hidden rounded-[24px] border border-white/15 shadow-deep ${artOf(work.work_id)}`} style={{ ["--lift" as string]: "6px" }}>
                <div className="absolute inset-0 bg-[linear-gradient(180deg,transparent_50%,rgb(4_5_12/0.9))]" />
                {work.phase && <span className="chip absolute top-5 left-5">PHASE {work.phase}</span>}
              </div>

              <section className="frost min-w-0 flex-1 rounded-[24px] px-[38px] pt-9 pb-8">
                <span className="text-[12.5px] font-semibold text-low">작품 소개</span>
                {work.summary ? (
                  <>
                    <p className="mt-3 text-lg leading-[1.8] whitespace-pre-wrap text-hi" lang="en">
                      {work.summary}
                    </p>
                    <p className="mt-6 flex items-center gap-1.5 text-xs text-low">
                      영문 위키백과 첫 문단{work.license && ` · ${work.license}`}
                      {work.source_url && (
                        <a href={work.source_url} target="_blank" rel="noopener noreferrer" className="ml-1 flex items-center gap-1 text-cyan hover:underline">
                          원문 보기 <IconExternalLink size={12} aria-hidden />
                        </a>
                      )}
                    </p>
                  </>
                ) : (
                  <p className="mt-3 text-mid">아직 소개 글이 없어요. 로어 AI에게 물어보세요.</p>
                )}
              </section>
            </div>
          </>
        )}
      </div>
    </>
  );
}
