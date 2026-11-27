"use client";

import { IconArrowLeft, IconHeart, IconPencil, IconSwords, IconTrash } from "@tabler/icons-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import Debate from "@/components/Debate";
import PageBanner, { useGrowStage } from "@/components/PageBanner";
import { ago, api, CATEGORIES, post, type Comment, type PostDetail } from "@/lib/api";

export default function PostPage() {
  return (
    <Suspense>
      <PostView />
    </Suspense>
  );
}

function PostView() {
  const id = useSearchParams().get("id") ?? "";
  const router = useRouter();
  const [data, setData] = useState<PostDetail | null>(null);
  const [error, setError] = useState(id ? "" : "글이 지정되지 않았어요.");
  const [notice, setNotice] = useState("");
  const main = useRef<HTMLDivElement>(null);
  useGrowStage(main, 1200);

  const reload = useCallback(
    () =>
      api<PostDetail>(`/posts/${encodeURIComponent(id)}`)
        .then(setData)
        .catch((e: Error) => setError(e.message)),
    [id],
  );
  useEffect(() => {
    if (id) reload();
  }, [id, reload]);

  async function act(run: () => Promise<unknown>, after?: () => void) {
    setNotice("");
    try {
      await run();
      after?.();
    } catch (e) {
      setNotice((e as Error).message);
    }
  }

  return (
    <>
      <div className="glow" data-depth="10" style={{ left: 60, top: 300, width: 520, height: 420, background: "#c2569a", opacity: 0.45 }} />
      <div className="glow" data-depth="14" style={{ left: 700, top: 120, width: 560, height: 360, background: "#6a4fd6", opacity: 0.55 }} />
      <div className="glow" data-depth="10" style={{ left: 1360, top: 520, width: 520, height: 460, background: "#2f7fd8", opacity: 0.5 }} />

      <div ref={main} className="absolute top-[150px] right-[120px] left-[120px] z-10">
        {!data ? (
          <p className="text-mid">{error ? <span className="text-amber">{error}</span> : "불러오는 중…"}</p>
        ) : (
          <>
            <PageBanner
              className="relative"
              parents={[{ href: "/community/", label: "커뮤니티" }]}
              crumb={CATEGORIES[data.category]}
              title={data.title}
              plainTitle
              desc={`${data.nickname} · ${ago(data.created_at)}`}
              aside={
                <div className="absolute top-10 right-10 flex gap-7 text-right">
                  {(
                    [
                      [data.like_count, "좋아요"],
                      [data.comments.length, "댓글"],
                    ] as const
                  ).map(([n, label]) => (
                    <div key={label}>
                      <b className="block text-[22px] font-bold text-hi tabular-nums">{n.toLocaleString()}</b>
                      <span className="text-[12.5px] text-low">{label}</span>
                    </div>
                  ))}
                </div>
              }
            >
              <button
                type="button"
                className="btn h-[42px] text-sm"
                onClick={() =>
                  act(async () => {
                    const r = await api<{ like_count: number; liked: boolean }>(`/posts/${data.id}/like`, { method: "POST" });
                    setData({ ...data, like_count: r.like_count });
                    if (!r.liked) setNotice("이미 좋아요를 눌렀어요.");
                  })
                }
              >
                <IconHeart size={16} className="text-[#ff8fb4]" aria-hidden />
                좋아요 {data.like_count}
              </button>
              {notice && (
                <p role="status" className="text-sm text-amber">
                  {notice}
                </p>
              )}
              <DeleteBox
                label="글 지우기"
                onDelete={(password) =>
                  act(
                    () => api(`/posts/${data.id}`, post({ password }, "DELETE")),
                    () => router.push("/community/"),
                  )
                }
              />
            </PageBanner>

            <div className="mt-6 flex items-start gap-6">
              <div className="w-[1100px] flex-none">
                <article className="frost rounded-[24px] px-[38px] pt-9 pb-8">
                  <span className="text-[12.5px] font-semibold text-low">본문</span>
                  <p className="mt-3 text-lg leading-[1.8] whitespace-pre-wrap break-words text-hi">{data.body}</p>
                  {data.category === "vs" && (
                    <div className="mt-8 rounded-[20px] border border-white/10 bg-black/18 p-7">
                      <Debate key={data.id} debate={data} />
                    </div>
                  )}
                </article>

                <section className="frost mt-6 rounded-[24px] px-[38px] pt-9 pb-8" aria-label="댓글">
                  <h2 className="text-xl font-bold text-hi">댓글 {data.comments.length}</h2>
                  <ul className="mt-5 flex flex-col">
                    {data.comments.map((c) => (
                      <CommentRow key={c.id} c={c} onDelete={(password) => act(() => api(`/comments/${c.id}`, post({ password }, "DELETE")), reload)} />
                    ))}
                  </ul>
                  <CommentForm postId={data.id} onDone={reload} />
                </section>
              </div>

              <nav className="frost min-w-0 flex-1 rounded-[24px] p-7" aria-label="커뮤니티 바로가기">
                <span className="text-[12.5px] font-semibold text-low">MULTIVERSE ARENA</span>
                <div className="mt-4 flex flex-col gap-2.5">
                  <Link href="/community/" className="btn h-11 justify-start text-sm">
                    <IconArrowLeft size={16} aria-hidden />
                    아레나로 돌아가기
                  </Link>
                  <Link href="/community/new/" className="btn h-11 justify-start text-sm">
                    <IconPencil size={16} aria-hidden />새 글 작성
                  </Link>
                  <Link href="/community/new/?category=vs" className="btn h-11 justify-start text-sm">
                    <IconSwords size={16} aria-hidden />
                    VS 토론 열기
                  </Link>
                </div>
              </nav>
            </div>
          </>
        )}
      </div>
    </>
  );
}

function CommentRow({ c, onDelete }: { c: Comment; onDelete: (password: string) => void }) {
  return (
    <li className="flex gap-4 border-t border-white/5 py-4 first:border-t-0">
      <div className="min-w-0 flex-1">
        <p className="text-sm text-low">
          <b className="text-ink">{c.nickname}</b> · {ago(c.created_at)}
        </p>
        <p className="mt-1.5 leading-relaxed whitespace-pre-wrap break-words">{c.body}</p>
      </div>
      <DeleteBox label="댓글 지우기" small onDelete={onDelete} />
    </li>
  );
}

// 지우기 버튼 → 비밀번호 입력칸이 펼쳐진다
function DeleteBox({ label, onDelete, small = false }: { label: string; onDelete: (password: string) => void; small?: boolean }) {
  const [open, setOpen] = useState(false);
  if (!open)
    return (
      <button type="button" onClick={() => setOpen(true)} className={`flex items-center gap-1.5 text-low hover:text-hi ${small ? "self-start text-sm" : "ml-auto"}`}>
        <IconTrash size={16} aria-hidden />
        {label}
      </button>
    );
  return (
    <form
      className={`flex items-center gap-2 ${small ? "self-start" : "ml-auto"}`}
      onSubmit={(e) => {
        e.preventDefault();
        onDelete(new FormData(e.currentTarget).get("password")?.toString() ?? "");
      }}
    >
      <input name="password" type="password" required autoFocus placeholder="비밀번호" aria-label={`${label} 비밀번호`} className="field h-10 w-40 py-0 text-sm" />
      <button type="submit" className="chip h-10 px-4 hover:bg-white/10">
        지우기
      </button>
      <button type="button" className="text-sm text-low hover:text-hi" onClick={() => setOpen(false)}>
        취소
      </button>
    </form>
  );
}

function CommentForm({ postId, onDone }: { postId: number; onDone: () => void }) {
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  return (
    <form
      className="mt-6 flex flex-col gap-3 border-t border-line pt-6"
      onSubmit={async (e) => {
        e.preventDefault();
        const form = e.currentTarget;
        setBusy(true);
        setError("");
        try {
          await api(`/posts/${postId}/comments`, post(Object.fromEntries(new FormData(form))));
          form.reset();
          onDone();
        } catch (err) {
          setError((err as Error).message);
        } finally {
          setBusy(false);
        }
      }}
    >
      <textarea name="body" required maxLength={1000} rows={3} placeholder="댓글을 남겨 보세요 (1,000자까지)" aria-label="댓글" className="field resize-none" />
      <div className="flex gap-3">
        <input name="nickname" required maxLength={20} placeholder="닉네임" aria-label="닉네임" className="field w-48" autoComplete="nickname" />
        <input name="password" type="password" required minLength={4} maxLength={64} placeholder="삭제용 비밀번호" aria-label="삭제용 비밀번호" className="field w-56" autoComplete="new-password" />
        <button type="submit" disabled={busy} className="btn btn-primary ml-auto h-[52px] text-base">
          댓글 달기
        </button>
      </div>
      {error && (
        <p role="alert" className="text-amber">
          {error}
        </p>
      )}
    </form>
  );
}
