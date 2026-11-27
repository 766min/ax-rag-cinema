"use client";

import { IconArrowLeft, IconSend2 } from "@tabler/icons-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useRef, useState } from "react";
import PageBanner, { TAB, useGrowStage } from "@/components/PageBanner";
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
  const main = useRef<HTMLDivElement>(null);
  useGrowStage(main, 1200);

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
    <>
      <div className="glow" data-depth="10" style={{ left: 60, top: 300, width: 520, height: 420, background: "#c2569a", opacity: 0.45 }} />
      <div className="glow" data-depth="14" style={{ left: 700, top: 120, width: 560, height: 360, background: "#6a4fd6", opacity: 0.55 }} />
      <div className="glow" data-depth="10" style={{ left: 1360, top: 520, width: 520, height: 460, background: "#2f7fd8", opacity: 0.5 }} />

      <div ref={main} className="absolute top-[150px] right-[120px] left-[120px] z-10">
        <PageBanner
          className="relative"
          parents={[{ href: "/community/", label: "커뮤니티" }]}
          crumb="새 글"
          title="NEW POST"
          desc={vs ? "두 선택지를 걸고 VS 토론을 열어요. 모두가 한 표씩 던질 수 있어요" : "세계관 떡밥, 쿠키 영상, 시청 순서 이야기를 자유롭게 남겨요"}
        >
          <nav className="flex gap-1.5" aria-label="분류">
            {Object.entries(CATEGORIES).map(([key, label]) => (
              <button key={key} type="button" aria-current={category === key ? "page" : undefined} onClick={() => setCategory(key as Category)} className={TAB}>
                {label}
              </button>
            ))}
          </nav>
        </PageBanner>

        <div className="mt-6 flex items-start gap-6">
          <form
            className="frost flex w-[1100px] flex-none flex-col gap-4 rounded-[24px] px-[38px] pt-9 pb-8"
            onSubmit={(e) => {
              e.preventDefault();
              submit(e.currentTarget);
            }}
          >
            <input type="hidden" name="category" value={category} />
            <label>
              <span className="mb-1.5 block text-sm text-mid">{vs ? "토론 주제 (100자까지)" : "제목 (100자까지)"}</span>
              <input name="title" required maxLength={100} className="field" placeholder={vs ? "예: 아이언맨의 진정한 후계자는 누구인가?" : "예: 타노스는 왜 스톤을 반만 썼을까?"} />
            </label>
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
            <label>
              <span className="mb-1.5 block text-sm text-mid">본문 (5,000자까지)</span>
              <textarea name="body" required maxLength={5000} rows={12} className="field resize-none leading-relaxed" placeholder={vs ? "왜 이 대결이 궁금한지, 판단 기준을 적어 주세요." : "이론, 쿠키 영상, 시청 순서 이야기를 자유롭게 적어 주세요."} />
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
          </form>

          <aside className="frost min-w-0 flex-1 rounded-[24px] p-7">
            <span className="text-[12.5px] font-semibold text-low">글쓰기 안내</span>
            <ul className="mt-4 flex list-disc flex-col gap-2.5 pl-5 text-sm leading-relaxed text-mid">
              <li>위 띠에서 분류를 고르세요. VS 토론을 고르면 선택지 두 칸이 생겨요.</li>
              <li>로그인 없이 쓰는 게시판이에요. 비밀번호는 글을 지울 때만 써요.</li>
              <li>비밀번호는 원래 글자 대신 해시로 바꿔 저장해요.</li>
            </ul>
            <Link href="/community/" className="btn mt-6 h-11 w-full justify-start text-sm">
              <IconArrowLeft size={16} aria-hidden />
              아레나로 돌아가기
            </Link>
          </aside>
        </div>
      </div>
    </>
  );
}
