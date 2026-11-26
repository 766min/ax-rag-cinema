import Link from "next/link";
import { workHref, workMeta, type Work } from "@/lib/api";

// 포스터 대신 작품마다 정해지는 CSS 그림 (art-0 ~ art-5)
export const artOf = (workId: string) =>
  `art-${[...workId].reduce((sum, ch) => sum + ch.charCodeAt(0), 0) % 6}`;

export default function MovieCard({ work, rank, className = "h-[290px] w-[278px]" }: { work: Work; rank?: number; className?: string }) {
  return (
    <Link
      href={workHref(work)}
      className={`tilt block flex-none overflow-hidden rounded-[22px] border border-white/15 shadow-deep focus-visible:outline-2 focus-visible:outline-cyan ${className}`}
    >
      <div className={`absolute inset-0 ${artOf(work.work_id)}`} />
      <div className="absolute inset-0 bg-[linear-gradient(180deg,transparent_40%,rgb(4_5_12/0.92))]" />
      {work.phase && <span className="chip absolute top-4 left-4 backdrop-blur">PHASE {work.phase}</span>}
      {rank && (
        <span className="absolute top-3 right-[18px] text-[44px] font-black text-transparent [-webkit-text-stroke:1.5px_rgb(255_255_255/0.5)]">
          {rank}
        </span>
      )}
      <div className="absolute right-5 bottom-[18px] left-5">
        <b className="block text-2xl leading-tight font-extrabold tracking-tight [text-shadow:0_4px_16px_rgb(0_0_0/0.8)]">{work.title_ko}</b>
        <span className="text-sm text-muted">{workMeta(work)}</span>
      </div>
    </Link>
  );
}
