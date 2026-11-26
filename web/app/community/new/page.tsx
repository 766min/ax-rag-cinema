"use client";

import { IconArrowLeft, IconSend2 } from "@tabler/icons-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { api, CATEGORIES, post, type Category, type Post } from "@/lib/api";

export default function NewPostPage() {
  return (
    <Suspense>
      <NewPost />
    </Suspense>
  );
}

function NewPost() {
  const router = useRouter();
  const preset = useSearchParams().get("category");
  const [category, setCategory] = useState<Category>(preset && preset in CATEGORIES ? (preset as Category) : "lore");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const vs = category === "vs";

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
    <div className="absolute top-[140px] bottom-10 left-1/2 z-10 flex w-[1000px] -translate-x-1/2 flex-col">
      <Link href="/community/" className="flex items-center gap-1.5 text-low hover:text-hi">
        <IconArrowLeft size={18} aria-hidden />
        멀티버스 아레나로
      </Link>
      <h1 className="mt-3 font-display text-[44px] leading-none tracking-[0.05em] text-hi">NEW POST</h1>

      <form
        className="frost mt-5 flex flex-1 flex-col gap-4 rounded-[26px] p-8"
        onSubmit={(e) => {
          e.preventDefault();
          submit(e.currentTarget);
        }}
      >
        <div className="flex gap-4">
          <label className="w-[220px]">
            <span className="mb-1.5 block text-sm text-mid">분류</span>
            <select name="category" required className="field" value={category} onChange={(e) => setCategory(e.target.value as Category)}>
              {Object.entries(CATEGORIES).map(([k, v]) => (
                <option key={k} value={k} className="bg-bg">
                  {v}
                </option>
              ))}
            </select>
          </label>
          <label className="flex-1">
            <span className="mb-1.5 block text-sm text-mid">{vs ? "토론 주제 (100자까지)" : "제목 (100자까지)"}</span>
            <input name="title" required maxLength={100} className="field" placeholder={vs ? "예: 아이언맨의 진정한 후계자는 누구인가?" : "예: 타노스는 왜 스톤을 반만 썼을까?"} />
          </label>
        </div>
        {vs && (
          <div className="grid grid-cols-2 gap-4">
            <label>
              <span className="mb-1.5 block text-sm text-amber">선택지 A (40자까지)</span>
              <input name="choice_a" required maxLength={40} className="field" placeholder="예: 스파이더맨 (피터 파커)" />
            </label>
            <label>
              <span className="mb-1.5 block text-sm text-cool">선택지 B (40자까지)</span>
              <input name="choice_b" required maxLength={40} className="field" placeholder="예: 캡틴 아메리카 (샘 윌슨)" />
            </label>
          </div>
        )}
        <label className="flex flex-1 flex-col">
          <span className="mb-1.5 block text-sm text-mid">본문 (5,000자까지)</span>
          <textarea name="body" required maxLength={5000} className="field flex-1 resize-none leading-relaxed" placeholder={vs ? "왜 이 대결이 궁금한지, 판단 기준을 적어 주세요." : "이론, 쿠키 영상, 시청 순서 이야기를 자유롭게 적어 주세요."} />
        </label>
        <div className="flex items-end gap-4">
          <label className="w-[240px]">
            <span className="mb-1.5 block text-sm text-mid">닉네임</span>
            <input name="nickname" required maxLength={20} className="field" autoComplete="nickname" />
          </label>
          <label className="w-[240px]">
            <span className="mb-1.5 block text-sm text-mid">삭제용 비밀번호 (4자 이상)</span>
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
        <p className="text-xs text-low">로그인 없이 쓰는 게시판이에요. 비밀번호는 글을 지울 때만 쓰고, 서버에는 원래 글자 대신 해시로 바꿔 저장해요.</p>
      </form>
    </div>
  );
}
