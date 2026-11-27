"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { artOf } from "@/components/MovieCard";
import PageBanner, { useGrowStage } from "@/components/PageBanner";
import { api, workHref, workMeta, type Work } from "@/lib/api";

// 세계관: 작중 시간순(timeline_order). 순서 값이 있는 작품만 줄 세운다
export default function UniversePage() {
  const [works, setWorks] = useState<Work[] | null>(null);
  const [error, setError] = useState("");
  const main = useRef<HTMLElement>(null);
  useGrowStage(main, 1200);

  useEffect(() => {
    api<Work[]>("/works?canon=true&sort=timeline")
      .then((w) => setWorks(w.filter((x) => x.timeline_order !== null)))
      .catch((e: Error) => setError(e.message));
  }, []);

  return (
    <>
      <div className="glow" data-depth="14" style={{ left: 700, top: 120, width: 560, height: 360, background: "#6a4fd6", opacity: 0.55 }} />
      <div className="glow" data-depth="10" style={{ left: 1360, top: 620, width: 520, height: 460, background: "#2f7fd8", opacity: 0.45 }} />

      <PageBanner crumb="세계관" title="SACRED TIMELINE" desc="개봉 순서가 아니라, 이야기 속 시간 순서대로 따라가요">
        <span className="text-sm text-mid">
          작중 시간순 <b className="font-semibold text-hi tabular-nums">{works?.length ?? "-"}</b>편
        </span>
        <Link href="/explore/" className="btn ml-auto h-[42px] text-sm">
          개봉순으로 보기
        </Link>
      </PageBanner>

      <section ref={main} className="absolute top-[474px] right-[120px] left-[120px] z-10">
        {error && <p className="text-amber">{error}</p>}
        {!works && !error && <p className="text-mid">불러오는 중…</p>}
        <ol className="relative ml-6 border-l-2 border-cyan/40">
          {works?.map((w) => (
            <li key={w.work_id} className="relative py-2.5 pl-10">
              <span className="absolute top-1/2 -left-[11px] size-5 -translate-y-1/2 rounded-full border-2 border-cyan bg-bg shadow-[0_0_12px_#5ee7ff]" />
              <Link href={workHref(w)} className="frost flex items-center gap-6 rounded-2xl p-4 transition-colors hover:bg-white/8">
                <span className={`size-16 flex-none rounded-xl ${artOf(w.work_id)}`} />
                <span className="w-12 text-2xl font-black text-cyan tabular-nums">{w.timeline_order}</span>
                <span>
                  <b className="block text-xl text-hi">{w.title_ko}</b>
                  <span className="text-sm text-mid">
                    {w.title_original} · {workMeta(w)}
                    {w.phase && ` · PHASE ${w.phase}`}
                  </span>
                </span>
              </Link>
            </li>
          ))}
        </ol>
      </section>
    </>
  );
}
