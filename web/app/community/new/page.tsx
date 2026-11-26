"use client";

import { IconArrowLeft, IconSend2 } from "@tabler/icons-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { api, CATEGORIES, post, type Post } from "@/lib/api";

export default function NewPostPage() {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit(form: HTMLFormElement) {
    const f = Object.fromEntries(new FormData(form)) as Record<string, string>;
    setBusy(true);
    setError("");
    try {
      const created = await api<Post>("/posts", post(f));
      router.push(`/community/post/?id=${created.id}`);
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }

  return (
    <div className="absolute top-[150px] bottom-[40px] left-1/2 z-10 flex w-[1000px] -translate-x-1/2 flex-col">
      <Link href="/community/" className="flex items-center gap-1.5 text-dim hover:text-ink">
        <IconArrowLeft size={18} aria-hidden />
        Cosmic Feed 로
      </Link>
      <h1 className="mt-4 text-[44px] font-extrabold tracking-tight">
        <span className="grad">새 글</span> 작성
      </h1>

      <form
        className="glass mt-6 flex flex-1 flex-col gap-4 p-8"
        onSubmit={(e) => {
          e.preventDefault();
          submit(e.currentTarget);
        }}
      >
        <div className="flex gap-4">
          <label className="w-[220px]">
            <span className="mb-1.5 block text-sm text-muted">분류</span>
            <select name="category" required className="field" defaultValue="lore">
              {Object.entries(CATEGORIES).map(([k, v]) => (
                <option key={k} value={k} className="bg-bg">
                  {v}
                </option>
              ))}
            </select>
          </label>
          <label className="flex-1">
            <span className="mb-1.5 block text-sm text-muted">제목 (100자까지)</span>
            <input name="title" required maxLength={100} className="field" placeholder="예: 타노스는 왜 스톤을 반만 썼을까?" />
          </label>
        </div>
        <label className="flex flex-1 flex-col">
          <span className="mb-1.5 block text-sm text-muted">본문 (5,000자까지)</span>
          <textarea name="body" required maxLength={5000} className="field flex-1 resize-none leading-relaxed" placeholder="이론, 쿠키 영상, 시청 순서 이야기를 자유롭게 적어 주세요." />
        </label>
        <div className="flex items-end gap-4">
          <label className="w-[240px]">
            <span className="mb-1.5 block text-sm text-muted">닉네임</span>
            <input name="nickname" required maxLength={20} className="field" autoComplete="nickname" />
          </label>
          <label className="w-[240px]">
            <span className="mb-1.5 block text-sm text-muted">삭제용 비밀번호 (4자 이상)</span>
            <input name="password" type="password" required minLength={4} maxLength={64} className="field" autoComplete="new-password" />
          </label>
          <button type="submit" disabled={busy} className="btn btn-primary ml-auto">
            <IconSend2 size={18} aria-hidden />
            {busy ? "올리는 중…" : "올리기"}
          </button>
        </div>
        {error && (
          <p role="alert" className="text-amber">
            {error}
          </p>
        )}
        <p className="text-xs text-dim">로그인 없이 쓰는 게시판이에요. 비밀번호는 글을 지울 때만 쓰고, 서버에는 원래 글자 대신 해시로 바꿔 저장해요.</p>
      </form>
    </div>
  );
}
