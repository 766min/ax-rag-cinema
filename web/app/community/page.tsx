"use client";

import { IconHeart, IconMessageCircle, IconPencil, IconSearch } from "@tabler/icons-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";
import { ago, api, CATEGORIES, type Category, type Post, type PostPage } from "@/lib/api";

const TOPICS = ["인피니티 스톤", "TVA", "와칸다", "양자 영역", "아스가르드", "멀티버스"];
const postHref = (p: Post) => `/community/post/?id=${p.id}`;

function fetchPage(page: number, category: Category | null, q: string) {
  const query = new URLSearchParams({ page: String(page) });
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
  const [pages, setPages] = useState<PostPage[]>([]);
  const [popular, setPopular] = useState<Post[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  // 탭 · 검색어가 바뀐 뒤에 늦게 도착한 이전 응답은 버린다
  const view = `${category}|${q}`;
  const current = useRef(view);
  useEffect(() => {
    current.current = view;
  }, [view]);

  const more = () => {
    setLoading(true);
    fetchPage(pages.length, category, q)
      .then((p) => current.current === view && setPages((prev) => [...prev, p]))
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    // 탭 · 검색어가 바뀌면 처음부터 다시
    let alive = true;
    fetchPage(0, category, q)
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
  }, [category, q]);

  useEffect(() => {
    api<PostPage>("/posts?sort=popular&limit=5")
      .then((p) => setPopular(p.items))
      .catch(() => setPopular([]));
  }, []);

  const posts = pages.flatMap((p) => p.items);
  const hasMore = pages.at(-1)?.has_more ?? false;
  const [feature, ...rest] = !q && !category && popular[0] ? [popular[0], ...posts.filter((p) => p.id !== popular[0].id)] : [null, ...posts];
  const go = (next: { category?: string; q?: string }) => {
    const query = new URLSearchParams({ category: category ?? "", q, ...next });
    [...query.keys()].forEach((k) => !query.get(k) && query.delete(k));
    router.replace(`/community/${query.size ? `?${query}` : ""}`);
  };

  return (
    <>
      <div className="orb ringed" data-depth="20" style={{ left: 1080, top: 150, width: 96, height: 96, ["--a" as string]: "120deg", ["--glow" as string]: "rgb(94 231 255/.45)" }} />

      <header className="absolute top-[150px] right-[120px] left-[120px] z-10 flex items-end">
        <div>
          <h1 className="text-[56px] font-extrabold tracking-tight">
            <span className="grad">Cosmic</span> Feed
          </h1>
          <p className="mt-2.5 text-lg text-muted">마블 팬들이 나누는 이론 · 쿠키 영상 · 시청 순서 이야기</p>
        </div>
        <Link href="/community/new/" className="btn btn-primary ml-auto h-[52px] text-base">
          <IconPencil size={18} aria-hidden />새 글 작성
        </Link>
      </header>

      <nav className="absolute top-[290px] right-[120px] left-[120px] z-10 flex items-end gap-9 border-b border-line" aria-label="게시판 분류">
        {[["", "전체"], ...Object.entries(CATEGORIES)].map(([key, label]) => (
          <button
            key={key || "all"}
            type="button"
            aria-current={(category ?? "") === key ? "page" : undefined}
            onClick={() => go({ category: key })}
            className="relative pb-3.5 text-base text-dim hover:text-ink aria-[current=page]:text-ink aria-[current=page]:after:absolute aria-[current=page]:after:right-0 aria-[current=page]:after:-bottom-px aria-[current=page]:after:left-0 aria-[current=page]:after:h-0.5 aria-[current=page]:after:bg-gradient-to-r aria-[current=page]:after:from-cyan aria-[current=page]:after:to-violet aria-[current=page]:after:content-['']"
          >
            {label}
          </button>
        ))}
        <form
          role="search"
          className="mb-2.5 ml-auto flex h-10 w-[320px] items-center gap-2 rounded-full border border-line bg-white/5 px-4 focus-within:border-cyan"
          onSubmit={(e) => {
            e.preventDefault();
            go({ q: new FormData(e.currentTarget).get("q")?.toString().trim() ?? "" });
          }}
        >
          <IconSearch size={16} className="text-dim" aria-hidden />
          <input key={q} name="q" defaultValue={q} placeholder="게시글 검색" aria-label="게시글 검색" className="w-full bg-transparent text-sm outline-none placeholder:text-dim" />
        </form>
      </nav>

      <section className="scroll-area absolute top-[366px] bottom-0 left-[120px] z-10 flex w-[1140px] flex-col gap-6 pr-3 pb-10 [perspective:1400px]">
        {error && <p className="text-amber">{error}</p>}
        {feature && (
          <Link href={postHref(feature)} className="glass tilt flex items-center gap-8 p-5" style={{ ["--lift" as string]: "6px" }}>
            <div className="relative h-[180px] w-[320px] flex-none overflow-hidden rounded-2xl bg-[radial-gradient(circle_at_50%_50%,#1b1450,#06061a_70%)] shadow-[0_20px_40px_rgb(0_0_0/0.55)] before:absolute before:top-1/2 before:left-1/2 before:-m-[100px] before:size-[200px] before:rounded-full before:bg-[conic-gradient(#5ee7ff,#ffb86b,#ff7ab6,#8b5cf6,#5ee7ff)] before:[mask:radial-gradient(closest-side,transparent_78%,#000_83%,transparent_96%)] before:content-['']" />
            <div className="min-w-0 flex-1">
              <div className="text-[13px] font-semibold text-cyan">인기 글 · {CATEGORIES[feature.category]}</div>
              <h3 className="mt-2.5 mb-2.5 line-clamp-2 text-[26px] leading-snug font-bold">{feature.title}</h3>
              <p className="line-clamp-2 leading-relaxed text-muted">{feature.body}</p>
              <Meta post={feature} />
            </div>
          </Link>
        )}

        <div className={`glass py-1.5 ${rest.length === 0 && (loading || posts.length > 0 || error) ? "hidden" : ""}`}>
          {rest.map((p) => (
            <Link key={p.id} href={postHref(p)} className="grid grid-cols-[110px_1fr_auto] items-center gap-6 border-t border-white/5 px-7 py-5 first:border-t-0 hover:bg-white/5">
              <span className="text-[13px] text-muted">{CATEGORIES[p.category]}</span>
              <div className="min-w-0">
                <h4 className="truncate text-lg font-semibold">{p.title}</h4>
                <p className="mt-1 truncate text-[14.5px] text-dim">{p.body}</p>
              </div>
              <Meta post={p} compact />
            </Link>
          ))}
          {!loading && posts.length === 0 && !error && (
            <p className="px-7 py-10 text-center text-muted">{q ? "검색 결과가 없어요." : "아직 글이 없어요. 첫 글을 남겨 보세요!"}</p>
          )}
        </div>
        {hasMore && (
          <button type="button" className="btn mx-auto h-12 text-base" disabled={loading} onClick={more}>
            더 보기
          </button>
        )}
      </section>

      <aside className="absolute top-[366px] left-[1300px] z-10 flex w-[500px] flex-col gap-7">
        <div className="glass px-7 py-[26px]">
          <h2 className="mb-[18px] text-[17px] font-bold">지금 뜨는 글</h2>
          <ol className="flex flex-col gap-4">
            {popular.map((p, i) => (
              <li key={p.id}>
                <Link href={postHref(p)} className="flex items-baseline gap-4 text-[#d6d9f7] hover:text-ink">
                  <em className="w-4 font-extrabold text-cyan not-italic">{i + 1}</em>
                  <span className="truncate">{p.title}</span>
                  <span className="ml-auto flex-none text-[13px] text-dim">♥ {p.like_count}</span>
                </Link>
              </li>
            ))}
            {popular.length === 0 && <li className="text-sm text-dim">아직 인기 글이 없어요</li>}
          </ol>
        </div>
        <div>
          <h2 className="mb-3.5 text-[17px] font-bold">유니버스 토픽</h2>
          <div className="flex flex-wrap gap-2">
            {TOPICS.map((t) => (
              <button key={t} type="button" className="chip hover:bg-white/10" onClick={() => go({ q: t, category: "" })}>
                #{t}
              </button>
            ))}
          </div>
        </div>
      </aside>
    </>
  );
}

function Meta({ post, compact = false }: { post: Post; compact?: boolean }) {
  return (
    <div className={`flex gap-[18px] text-sm text-dim ${compact ? "" : "mt-4"}`}>
      <span>
        {compact ? "" : `${post.nickname} · `}
        {ago(post.created_at)}
      </span>
      <span className="flex items-center gap-1">
        <IconMessageCircle size={15} aria-label="댓글" />
        {post.comment_count}
      </span>
      <span className="flex items-center gap-1">
        <IconHeart size={15} aria-label="좋아요" />
        {post.like_count}
      </span>
    </div>
  );
}
