"use client";

import { IconArrowLeft, IconExternalLink, IconSparkles } from "@tabler/icons-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { useChat } from "@/components/ChatPanel";
import { artOf } from "@/components/MovieCard";
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

  useEffect(() => {
    if (!id) return;
    api<WorkDetail>(`/works/${encodeURIComponent(id)}`)
      .then(setWork)
      .catch((e: Error) => setError(e.message));
  }, [id]);

  return (
    <div className="absolute top-[150px] right-[120px] bottom-[40px] left-[120px] z-10 flex gap-16">
      {(error || !id) && <p className="text-amber">{error || "작품이 지정되지 않았어요."}</p>}
      {!work && !error && id && <p className="text-muted">불러오는 중…</p>}
      {work && (
        <>
          <div className={`tilt relative h-[700px] w-[520px] flex-none overflow-hidden rounded-[28px] border border-white/15 shadow-deep ${artOf(work.work_id)}`} style={{ ["--lift" as string]: "6px" }}>
            <div className="absolute inset-0 bg-[linear-gradient(180deg,transparent_50%,rgb(4_5_12/0.9))]" />
            {work.phase && <span className="chip absolute top-5 left-5">PHASE {work.phase}</span>}
          </div>

          <div className="flex min-w-0 flex-1 flex-col">
            <Link href="/explore/" className="flex items-center gap-1.5 text-dim hover:text-ink">
              <IconArrowLeft size={18} aria-hidden />
              탐색으로
            </Link>
            <h1 className="mt-6 text-[64px] leading-tight font-extrabold tracking-tight">{work.title_ko}</h1>
            <p className="mt-2 text-2xl text-muted">{work.title_original}</p>
            <dl className="mt-8 flex flex-wrap gap-3">
              {[
                ["공개", work.year ?? "미정"],
                ["형식", formatKo(work.format)],
                ["페이즈", work.phase ? `PHASE ${work.phase}` : "-"],
                ["작중 시간순", work.timeline_order ?? "-"],
              ].map(([k, v]) => (
                <div key={k} className="glass rounded-2xl px-5 py-3">
                  <dt className="text-xs text-dim">{k}</dt>
                  <dd className="mt-1 text-lg font-semibold">{v}</dd>
                </div>
              ))}
            </dl>

            <section className="glass scroll-area mt-8 max-h-[340px] rounded-3xl p-7">
              <h2 className="text-lg font-bold">작품 소개</h2>
              {work.summary ? (
                <>
                  <p className="mt-3 leading-relaxed whitespace-pre-wrap text-[#d6d9f7]" lang="en">
                    {work.summary}
                  </p>
                  <p className="mt-4 flex items-center gap-1.5 text-xs text-dim">
                    영문 위키백과 첫 문단{work.license && ` · ${work.license}`}
                    {work.source_url && (
                      <a href={work.source_url} target="_blank" rel="noopener noreferrer" className="ml-1 flex items-center gap-1 text-cyan hover:underline">
                        원문 보기 <IconExternalLink size={12} aria-hidden />
                      </a>
                    )}
                  </p>
                </>
              ) : (
                <p className="mt-3 text-muted">아직 소개 글이 없어요. 로어 AI에게 물어보세요.</p>
              )}
            </section>

            <div className="mt-8 flex items-center gap-5">
              <button type="button" className="btn btn-primary" onClick={() => chat.open({ id: work.work_id, title: work.title_ko })}>
                <IconSparkles size={20} aria-hidden />이 작품에 대해 로어 AI에게 묻기
              </button>
              <span className="text-sm text-dim">시네버스는 작품 정보만 보여 줘요 (영상 재생은 없어요)</span>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
