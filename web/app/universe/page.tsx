"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { artOf } from "@/components/MovieCard";
import { api, workHref, workMeta, type Work } from "@/lib/api";

// 세계관: 작중 시간순(timeline_order). 순서 값이 있는 작품만 줄 세운다
export default function UniversePage() {
  const [works, setWorks] = useState<Work[] | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api<Work[]>("/works?canon=true&sort=timeline")
      .then((w) => setWorks(w.filter((x) => x.timeline_order !== null)))
      .catch((e: Error) => setError(e.message));
  }, []);

  return (
    <div className="absolute top-[150px] right-[120px] bottom-0 left-[120px] z-10 flex flex-col">
      <header>
        <h1 className="text-[56px] font-extrabold tracking-tight">
          <span className="grad">세계관</span> 타임라인
        </h1>
        <p className="mt-2.5 text-lg text-muted">개봉 순서가 아니라, 이야기 속 시간 순서대로 따라가요</p>
      </header>

      <div className="scroll-area mt-8 -mr-4 flex-1 pr-4 pb-10">
        {error && <p className="text-amber">{error}</p>}
        {!works && !error && <p className="text-muted">불러오는 중…</p>}
        <ol className="relative ml-6 border-l-2 border-cyan/40">
          {works?.map((w) => (
            <li key={w.work_id} className="relative py-2.5 pl-10">
              <span className="absolute top-1/2 -left-[11px] size-5 -translate-y-1/2 rounded-full border-2 border-cyan bg-bg shadow-[0_0_12px_#5ee7ff]" />
              <Link href={workHref(w)} className="glass flex items-center gap-6 rounded-2xl p-4 hover:border-line-hi">
                <span className={`size-16 flex-none rounded-xl ${artOf(w.work_id)}`} />
                <span className="w-12 text-2xl font-black text-cyan">{w.timeline_order}</span>
                <span>
                  <b className="block text-xl">{w.title_ko}</b>
                  <span className="text-sm text-muted">
                    {w.title_original} · {workMeta(w)}
                    {w.phase && ` · PHASE ${w.phase}`}
                  </span>
                </span>
              </Link>
            </li>
          ))}
        </ol>
      </div>
    </div>
  );
}
