"use client";

import Link from "next/link";
import { useState } from "react";
import { api, ApiError, post, type Post } from "@/lib/api";

// VS 토론: 두 선택지에 한 표. 진행바가 실제 비율로 차오르고, 투표하면 VS 배지가 경계를 따라 움직인다
// link: 제목을 글로 가는 링크로 (게시판 첫 화면) · 글 화면에서는 제목이 이미 위에 있어서 뺀다
export default function Debate({ debate, link = false }: { debate: Post; link?: boolean }) {
  const [state, setState] = useState({ a: debate.votes_a, b: debate.votes_b, mine: debate.my_vote });
  const [error, setError] = useState("");
  const total = state.a + state.b;
  const pa = total ? Math.round((state.a / total) * 100) : 50;
  const pb = 100 - pa;

  async function vote(side: "a" | "b") {
    if (state.mine === side) return;
    setError("");
    try {
      const r = await api<{ votes_a: number; votes_b: number; my_vote: "a" | "b" }>(`/posts/${debate.id}/vote`, post({ side }));
      setState({ a: r.votes_a, b: r.votes_b, mine: r.my_vote });
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "투표하지 못했어요.");
    }
  }

  const choice = (side: "a" | "b", label: string | null) => {
    const mine = state.mine === side;
    return (
      <button
        type="button"
        onClick={() => vote(side)}
        aria-pressed={mine}
        className={`rounded-[14px] border px-4 py-3.5 transition hover:-translate-y-px hover:border-white/24 hover:bg-white/6 ${side === "b" ? "text-right" : "text-left"} ${
          mine
            ? side === "a"
              ? "border-[rgb(255_138_92/0.6)] bg-[rgb(255_138_92/0.08)]"
              : "border-[rgb(111_178_255/0.6)] bg-[rgb(111_178_255/0.08)]"
            : "border-white/12 bg-white/3"
        }`}
      >
        <b className="block text-[16.5px] font-bold text-hi">{label}</b>
        <span className={`mt-2.5 inline-block text-[12.5px] font-semibold ${side === "a" ? "text-amber" : "text-cool"}`}>
          {mine ? "내 선택" : state.mine ? "이쪽으로 바꾸기" : "이쪽에 한 표"}
        </span>
      </button>
    );
  };

  return (
    <div>
      <div className="flex items-center gap-2.5">
        <span className="inline-flex h-6 items-center gap-1.5 rounded-full border border-[rgb(255_138_92/0.35)] bg-[rgb(255_110_80/0.16)] px-2.5 text-xs font-semibold text-[#ffd2c4] before:size-1.5 before:animate-[pulse-dot_1.6s_ease-in-out_infinite] before:rounded-full before:bg-[#ff6a4a] before:content-['']">
          LIVE 토론
        </span>
        <span className="text-[12.5px] font-semibold text-low">VS 토론</span>
        <span className="ml-auto text-[13px] text-low tabular-nums">{total.toLocaleString()}명 참여</span>
      </div>
      {link && (
        <h2 className="mt-2.5 text-[22px] font-bold tracking-tight text-hi">
          <Link href={`/community/post/?id=${debate.id}`} className="hover:underline">
            {debate.title}
          </Link>
        </h2>
      )}
      <div className="mt-[22px] grid grid-cols-2 gap-3">
        {choice("a", debate.choice_a)}
        {choice("b", debate.choice_b)}
      </div>
      <div className="mt-[26px] grid grid-cols-[70px_1fr_70px] items-center gap-3.5">
        <span className="text-2xl font-extrabold tracking-tight text-amber tabular-nums">{pa}%</span>
        <div
          className="relative h-3.5 rounded-full bg-white/8 shadow-[inset_0_1px_2px_rgb(0_0_0/0.4)]"
          role="img"
          aria-label={`${debate.choice_a} ${pa}% 대 ${debate.choice_b} ${pb}%`}
        >
          <i className="absolute inset-y-0 left-0 rounded-l-full bg-gradient-to-r from-[#ff6a4a] to-amber shadow-[0_0_16px_rgb(255_138_92/0.45)] transition-[width] duration-900" style={{ width: `${pa}%` }} />
          <i className="absolute inset-y-0 right-0 rounded-r-full bg-gradient-to-r from-cool to-cool2 shadow-[0_0_16px_rgb(111_178_255/0.45)] transition-[width] duration-900" style={{ width: `${pb}%` }} />
          <span
            className="absolute top-1/2 -mt-[19px] -ml-[19px] grid size-[38px] place-items-center rounded-full bg-hi font-display text-sm tracking-[0.04em] text-[#111] shadow-[0_0_0_4px_rgb(7_7_11/0.8),0_6px_16px_rgb(0_0_0/0.5)] transition-[left] duration-900"
            style={{ left: `${pa}%` }}
          >
            VS
          </span>
        </div>
        <span className="text-right text-2xl font-extrabold tracking-tight text-cool tabular-nums">{pb}%</span>
      </div>
      <div className="mt-2 flex justify-between px-[84px] text-[12.5px] text-low">
        <span>{state.a.toLocaleString()}표</span>
        <span>{state.b.toLocaleString()}표</span>
      </div>
      {error && (
        <p role="alert" className="mt-3 text-sm text-amber">
          {error}
        </p>
      )}
    </div>
  );
}
