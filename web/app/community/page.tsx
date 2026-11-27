"use client";

import { IconArrowRight, IconHeart, IconMessageCircle, IconPencil, IconSearch, IconShare3 } from "@tabler/icons-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";
import Debate from "@/components/Debate";
import PageBanner, { SEARCH, TAB, useGrowStage } from "@/components/PageBanner";
import { ago, api, CATEGORIES, type BoardStats, type Category, type Post, type PostPage } from "@/lib/api";

const TOPICS = ["인피니티 스톤", "TVA", "와칸다", "양자 영역", "멀티버스", "쿠키 영상"];
const SORTS = { new: "최신순", popular: "인기순", comments: "댓글 많은 순" } as const;
type Sort = keyof typeof SORTS;
const postHref = (p: Post) => `/community/post/?id=${p.id}`;
const MIN_HEIGHT = 1540; // 무대 높이 (글이 많으면 더 길어진다)

// 닉네임마다 정해지는 아바타 색
const AVATARS = [
  "linear-gradient(135deg,#ffc46b,#ff6a4a)",
  "linear-gradient(135deg,#f3e08a,#c9962a)",
  "linear-gradient(135deg,#a9d0ff,#6f6bff)",
  "linear-gradient(135deg,#f4a9d8,#b8a4f6)",
  "linear-gradient(135deg,#9be7b8,#2f9e7a)",
];
const avatarOf = (name: string) => AVATARS[[...name].reduce((s, c) => s + c.charCodeAt(0), 0) % AVATARS.length];

function fetchPage(page: number, category: Category | null, q: string, sort: Sort) {
  const query = new URLSearchParams({ page: String(page), sort });
  if (category) query.set("category", category);
  if (q) query.set("q", q);
  return api<PostPage>(`/posts?${query}`);
}

export default function CommunityPage() {
  return (
    <Suspense>
      <Community />
    </Suspense>
  );
}

function Community() {
  const router = useRouter();
  const params = useSearchParams();
  const raw = params.get("category");
  const category = raw && raw in CATEGORIES ? (raw as Category) : null;
  const q = params.get("q") ?? "";
  const sortRaw = params.get("sort");
  const sort: Sort = sortRaw && sortRaw in SORTS ? (sortRaw as Sort) : "new";
  const [pages, setPages] = useState<PostPage[]>([]);
  const [debate, setDebate] = useState<Post | null | undefined>(undefined);
  const [hot, setHot] = useState<Post | null>(null);
  const [popular, setPopular] = useState<Post[]>([]);
  const [stats, setStats] = useState<BoardStats | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const main = useRef<HTMLElement>(null);

  // 탭 · 검색어 · 정렬이 바뀐 뒤에 늦게 도착한 이전 응답은 버린다
  const view = `${category}|${q}|${sort}`;
  const current = useRef(view);
  useEffect(() => {
    current.current = view;
  }, [view]);

  const more = () => {
    setLoading(true);
    fetchPage(pages.length, category, q, sort)
      .then((p) => current.current === view && setPages((prev) => [...prev, p]))
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    let alive = true;
    fetchPage(0, category, q, sort)
      .then((p) => {
        if (!alive) return;
        setPages([p]);
        setError("");
      })
      .catch((e: Error) => alive && setError(e.message))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [category, q, sort]);

  useEffect(() => {
    // 이번 주 가장 많이 투표한 VS 토론 (없으면 가장 최근 VS 토론)
    api<PostPage>("/posts?category=vs&sort=votes&days=7&limit=1")
      .then((p) => p.items[0] ?? api<PostPage>("/posts?category=vs&limit=1").then((x) => x.items[0] ?? null))
      .then(setDebate)
      .catch(() => setDebate(null));
    api<PostPage>("/posts?sort=comments&days=7&limit=1")
      .then((p) => setHot(p.items[0] ?? null))
      .catch(() => setHot(null));
    api<PostPage>("/posts?sort=popular&limit=3")
      .then((p) => setPopular(p.items))
      .catch(() => setPopular([]));
    api<BoardStats>("/board/stats")
      .then(setStats)
      .catch(() => setStats(null));
  }, []);

  useGrowStage(main, MIN_HEIGHT);

  const posts = pages.flatMap((p) => p.items);
  const hasMore = pages.at(-1)?.has_more ?? false;
  const go = (next: { category?: string; q?: string; sort?: string }) => {
    const query = new URLSearchParams({ category: category ?? "", q, sort: sort === "new" ? "" : sort, ...next });
    [...query.keys()].forEach((k) => !query.get(k) && query.delete(k));
    router.replace(`/community/${query.size ? `?${query}` : ""}`, { scroll: false });
  };

  return (
    <>
      <div className="glow" data-depth="10" style={{ left: 60, top: 300, width: 520, height: 420, background: "#c2569a", opacity: 0.55 }} />
      <div className="glow" data-depth="14" style={{ left: 700, top: 120, width: 560, height: 360, background: "#6a4fd6", opacity: 0.55 }} />
      <div className="glow" data-depth="10" style={{ left: 1360, top: 520, width: 520, height: 460, background: "#2f7fd8", opacity: 0.55 }} />
      <div className="glow" data-depth="12" style={{ left: 420, top: 760, width: 620, height: 300, background: "#d9763a", opacity: 0.32 }} />

      <PageBanner
        crumb="커뮤니티"
        title="MULTIVERSE ARENA"
        desc="정답은 없다, 과몰입만 있을 뿐. 최애 히어로 대결부터 세계관 떡밥 토론까지"
        aside={
          <div className="absolute top-10 right-10 flex gap-7 text-right">
            {(
              [
                [stats?.today_posts, "오늘 올라온 글"],
                [stats?.open_debates, "진행 중 토론"],
                [stats?.week_participation, "이번 주 참여"],
              ] as const
            ).map(([n, label]) => (
              <div key={label}>
                <b className="block text-[22px] font-bold text-hi tabular-nums">{n?.toLocaleString() ?? "-"}</b>
                <span className="text-[12.5px] text-low">{label}</span>
              </div>
            ))}
          </div>
        }
      >
        <nav className="flex gap-1.5" aria-label="분류">
          {[["", "전체"], ...Object.entries(CATEGORIES)].map(([key, label]) => (
            <button
              key={key || "all"}
              type="button"
              aria-current={(category ?? "") === key ? "page" : undefined}
              onClick={() => go({ category: key })}
              className={TAB}
            >
              {label}
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
          <input key={q} name="q" defaultValue={q} placeholder="게시글 검색" aria-label="게시글 검색" className="w-full bg-transparent text-sm text-ink outline-none placeholder:text-low" />
        </form>
        <Link href="/community/new/" className="btn btn-primary h-[42px] text-sm">
          <IconPencil size={16} aria-hidden />새 글 작성
        </Link>
      </PageBanner>

      <section ref={main} className="absolute top-[474px] left-[120px] z-10 w-[1100px]">
        <div className="frost rounded-[24px] px-[38px] pt-9 pb-8">
          {debate ? (
            <Debate key={debate.id} debate={debate} link />
          ) : debate === null ? (
            <div className="flex items-center">
              <div>
                <span className="text-[12.5px] font-semibold text-low">이번 주 대결</span>
                <h2 className="mt-2 text-[22px] font-bold text-hi">아직 열린 VS 토론이 없어요</h2>
                <p className="mt-1 text-sm text-mid">최애 히어로 대결을 직접 열어 보세요. 두 선택지에 모두가 한 표씩 던질 수 있어요.</p>
              </div>
              <Link href="/community/new/?category=vs" className="btn ml-auto h-11 text-sm">
                VS 토론 열기 <IconArrowRight size={16} aria-hidden />
              </Link>
            </div>
          ) : (
            <p className="text-mid">불러오는 중…</p>
          )}
        </div>

        <div className="mt-16 mb-[18px] flex items-baseline">
          <span className="text-[12.5px] font-semibold text-low">{q ? `"${q}" 검색 결과` : "최근 글"}</span>
          <span className="ml-auto flex gap-2 text-[13px] text-low">
            {Object.entries(SORTS).map(([key, label], i) => (
              <span key={key} className="flex gap-2">
                {i > 0 && "·"}
                <button type="button" onClick={() => go({ sort: key })} aria-pressed={sort === key} className="hover:text-hi aria-pressed:font-semibold aria-pressed:text-hi">
                  {label}
                </button>
              </span>
            ))}
          </span>
        </div>

        {error && <p className="text-amber">{error}</p>}
        <div className="flex flex-col gap-6">
          {posts.map((p) => (
            <article key={p.id} className="frost relative grid grid-cols-[44px_1fr] gap-[18px] rounded-[18px] px-[30px] py-[26px] transition hover:-translate-y-px hover:border-white/24 hover:bg-white/8">
              <span className="grid size-11 place-items-center rounded-full text-[15px] font-bold text-[#111]" style={{ background: avatarOf(p.nickname) }} aria-hidden>
                {[...p.nickname][0]}
              </span>
              <div className="min-w-0">
                <div className="flex items-center gap-1.5 text-[13.5px] text-low">
                  <b className="text-[14.5px] font-bold text-hi">{p.nickname}</b>
                  <span className="rounded-full border border-white/12 px-2 py-px text-xs text-mid">{CATEGORIES[p.category]}</span>· {ago(p.created_at)}
                </div>
                <h3 className="mt-1 text-[16.5px] font-bold tracking-tight text-hi">
                  {/* 글 전체를 누르면 열리게 (제목 링크를 카드 크기로 넓힘) */}
                  <Link href={postHref(p)} className="after:absolute after:inset-0 after:content-[''] focus-visible:outline-2 focus-visible:outline-[#9fc2ff]">
                    {p.title}
                  </Link>
                </h3>
                <p className="mt-0.5 line-clamp-1 text-sm leading-[1.55] text-mid">{p.body}</p>
                {p.category === "vs" && <MiniPoll a={p.votes_a} b={p.votes_b} labels={[p.choice_a, p.choice_b]} />}
                <div className="relative z-10 mt-3.5 flex gap-[22px] text-[13px] text-low">
                  <span className="inline-flex items-center gap-1.5">
                    <IconHeart size={17} aria-label="좋아요" />
                    {p.like_count}
                  </span>
                  <span className="inline-flex items-center gap-1.5">
                    <IconMessageCircle size={17} aria-label="댓글" />
                    {p.comment_count}
                  </span>
                  <ShareButton href={postHref(p)} />
                </div>
              </div>
            </article>
          ))}
          {!loading && posts.length === 0 && !error && (
            <p className="frost rounded-[18px] px-7 py-10 text-center text-mid">{q ? "검색 결과가 없어요." : "아직 글이 없어요. 첫 글을 남겨 보세요!"}</p>
          )}
        </div>
        {hasMore && (
          <button type="button" className="btn mx-auto mt-6 flex h-12 text-base" disabled={loading} onClick={more}>
            더 보기
          </button>
        )}
      </section>

      <aside className="absolute top-[474px] left-[1300px] z-10 flex w-[500px] flex-col gap-7">
        {hot && (
          <div className="frost rounded-[24px] px-8 py-[30px]">
            <span className="text-[12.5px] font-semibold text-low">지금 가장 뜨거운 글</span>
            <h4 className="mt-2 text-[17px] leading-[1.45] font-bold text-hi">{hot.title}</h4>
            <p className="mt-1.5 text-[13px] text-low">
              댓글 {hot.comment_count}개 · 좋아요 {hot.like_count}개
            </p>
            <Link href={postHref(hot)} className="mt-3 inline-flex h-9 items-center gap-1.5 rounded-[10px] bg-hi px-3.5 text-[13.5px] font-semibold text-[#111]">
              토론 참여하기 <IconArrowRight size={15} aria-hidden />
            </Link>
          </div>
        )}
        <div className="frost rounded-[24px] px-8 py-[30px]">
          <span className="text-[12.5px] font-semibold text-low">주요 태그</span>
          <div className="mt-3 flex flex-wrap gap-1.5">
            {TOPICS.map((t) => (
              <button key={t} type="button" className="chip text-mid transition-colors hover:bg-white/10 hover:text-hi" onClick={() => go({ q: t, category: "" })}>
                {t}
              </button>
            ))}
          </div>
        </div>
        <div className="frost rounded-[24px] px-8 py-[30px]">
          <span className="text-[12.5px] font-semibold text-low">인기 글</span>
          <ol className="mt-2">
            {popular.map((p, i) => (
              <li key={p.id} className="border-t border-white/7 first:border-t-0">
                <Link href={postHref(p)} className="flex gap-3 py-[9px] text-sm text-mid hover:text-hi">
                  <em className="w-3.5 text-low not-italic tabular-nums">{i + 1}</em>
                  <span className="truncate">{p.title}</span>
                </Link>
              </li>
            ))}
            {popular.length === 0 && <li className="py-2 text-sm text-low">아직 인기 글이 없어요</li>}
          </ol>
        </div>
      </aside>
    </>
  );
}

function MiniPoll({ a, b, labels }: { a: number; b: number; labels: [string | null, string | null] }) {
  const total = a + b;
  const pa = total ? Math.round((a / total) * 100) : 50;
  return (
    <div className="mt-2 flex items-center gap-2.5 text-[12.5px] text-mid tabular-nums">
      {labels[0]} {pa}%
      <span className="relative h-1 flex-[0_0_180px] overflow-hidden rounded-full bg-gradient-to-r from-cool to-cool2">
        <i className="absolute inset-y-0 left-0 bg-gradient-to-r from-[#ff6a4a] to-amber" style={{ width: `${pa}%` }} />
      </span>
      {labels[1]} {100 - pa}% · 투표 {total.toLocaleString()}
    </div>
  );
}

// 글 주소를 복사한다 (공유)
function ShareButton({ href }: { href: string }) {
  const [done, setDone] = useState(false);
  return (
    <button
      type="button"
      className="inline-flex items-center gap-1.5 hover:text-hi"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(new URL(href, location.origin).toString());
          setDone(true);
          setTimeout(() => setDone(false), 1500);
        } catch {
          setDone(false);
        }
      }}
    >
      <IconShare3 size={17} aria-hidden />
      {done ? "주소 복사됨" : "공유"}
    </button>
  );
}
