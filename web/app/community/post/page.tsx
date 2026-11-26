"use client";

import { IconArrowLeft, IconHeart, IconTrash } from "@tabler/icons-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useState } from "react";
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

  if (!data)
    return <p className="absolute top-[160px] left-[120px] z-10 text-muted">{error ? <span className="text-amber">{error}</span> : "불러오는 중…"}</p>;

  return (
    <div className="scroll-area absolute top-[150px] bottom-0 left-1/2 z-10 w-[1100px] -translate-x-1/2 pr-3 pb-10">
      <Link href="/community/" className="flex items-center gap-1.5 text-dim hover:text-ink">
        <IconArrowLeft size={18} aria-hidden />
        Cosmic Feed 로
      </Link>

      <article className="glass mt-5 p-10">
        <div className="text-[13px] font-semibold text-cyan">{CATEGORIES[data.category]}</div>
        <h1 className="mt-3 text-[40px] leading-snug font-extrabold tracking-tight">{data.title}</h1>
        <p className="mt-3 text-sm text-dim">
          {data.nickname} · {ago(data.created_at)}
        </p>
        <p className="mt-8 text-lg leading-[1.8] whitespace-pre-wrap break-words text-[#e3e6ff]">{data.body}</p>

        <div className="mt-10 flex items-center gap-3">
          <button
            type="button"
            className="btn h-12 text-base"
            onClick={() =>
              act(async () => {
                const r = await api<{ like_count: number; liked: boolean }>(`/posts/${data.id}/like`, { method: "POST" });
                setData({ ...data, like_count: r.like_count });
                if (!r.liked) setNotice("이미 좋아요를 눌렀어요.");
              })
            }
          >
            <IconHeart size={18} className="text-magenta" aria-hidden />
            좋아요 {data.like_count}
          </button>
          <DeleteBox
            label="글 지우기"
            onDelete={(password) =>
              act(
                () => api(`/posts/${data.id}`, post({ password }, "DELETE")),
                () => router.push("/community/"),
              )
            }
          />
        </div>
        {notice && (
          <p role="status" className="mt-4 text-amber">
            {notice}
          </p>
        )}
      </article>

      <section className="glass mt-6 p-8" aria-label="댓글">
        <h2 className="text-xl font-bold">댓글 {data.comments.length}</h2>
        <ul className="mt-5 flex flex-col">
          {data.comments.map((c) => (
            <CommentRow key={c.id} c={c} onDelete={(password) => act(() => api(`/comments/${c.id}`, post({ password }, "DELETE")), reload)} />
          ))}
        </ul>
        <CommentForm postId={data.id} onDone={reload} />
      </section>
    </div>
  );
}

function CommentRow({ c, onDelete }: { c: Comment; onDelete: (password: string) => void }) {
  return (
    <li className="flex gap-4 border-t border-white/5 py-4 first:border-t-0">
      <div className="min-w-0 flex-1">
        <p className="text-sm text-dim">
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
      <button type="button" onClick={() => setOpen(true)} className={`flex items-center gap-1.5 text-dim hover:text-ink ${small ? "self-start text-sm" : "ml-auto"}`}>
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
      <button type="button" className="text-sm text-dim hover:text-ink" onClick={() => setOpen(false)}>
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
