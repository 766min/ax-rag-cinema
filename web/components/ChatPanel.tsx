"use client";

import {
  IconAlertTriangle,
  IconCircleCheck,
  IconExternalLink,
  IconSearch,
  IconSend2,
  IconSparkles,
  IconWorld,
  IconX,
} from "@tabler/icons-react";
import { createContext, Fragment, useContext, useEffect, useRef, useState } from "react";
import { api, ApiError, post, type AskData, type Source } from "@/lib/api";

// ---------- 열기 · 닫기 (어느 화면에서든 헤더 버튼 · 영화 화면에서 연다) ----------

type WorkContext = { id: string; title: string } | null;
type Msg = { role: "me"; text: string } | { role: "ai"; data?: AskData; error?: string };
type Chat = {
  isOpen: boolean;
  work: WorkContext;
  open: (work?: WorkContext) => void;
  close: () => void;
  setWork: (work: WorkContext) => void;
  messages: Msg[];
  setMessages: React.Dispatch<React.SetStateAction<Msg[]>>;
};

const ChatContext = createContext<Chat | null>(null);

export function ChatProvider({ children }: { children: React.ReactNode }) {
  const [isOpen, setOpen] = useState(false);
  const [work, setWork] = useState<WorkContext>(null);
  const [messages, setMessages] = useState<Msg[]>([]); // 화면을 옮겨도 대화가 남는다 (새로고침하면 사라짐)
  const value: Chat = {
    isOpen,
    work,
    messages,
    setMessages,
    setWork,
    open: (w = null) => {
      setWork(w);
      setOpen(true);
    },
    close: () => setOpen(false),
  };
  return <ChatContext.Provider value={value}>{children}</ChatContext.Provider>;
}

export function useChat(): Chat {
  const chat = useContext(ChatContext);
  if (!chat) throw new Error("ChatProvider 안에서만 쓸 수 있어요");
  return chat;
}

// ---------- 패널 ----------

const PROMPTS = ["타노스의 목적은 뭐야?", "타임 스톤은 어떤 힘이 있어?", "토니 스타크는 동굴에서 뭘 만들었어?"];

export default function ChatPanel() {
  const { isOpen, close, work, setWork, messages, setMessages } = useChat();
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const input = useRef<HTMLTextAreaElement>(null);
  const bottom = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (isOpen) input.current?.focus();
  }, [isOpen]);
  useEffect(() => {
    bottom.current?.scrollIntoView({ block: "end" }); // 요즘 브라우저는 Promise 를 돌려줘서 정리 함수로 넘기면 안 된다
  }, [messages, busy]);
  useEffect(() => {
    const esc = (e: KeyboardEvent) => e.key === "Escape" && close();
    addEventListener("keydown", esc);
    return () => removeEventListener("keydown", esc);
  }, [close]);

  async function ask(question: string) {
    const q = question.trim();
    if (!q || busy) return;
    setText("");
    setBusy(true);
    setMessages((m) => [...m, { role: "me", text: q }]);
    try {
      const data = await api<AskData>("/ask", post({ question: q, work_id: work?.id ?? null }));
      setMessages((m) => [...m, { role: "ai", data }]);
    } catch (e) {
      setMessages((m) => [...m, { role: "ai", error: e instanceof ApiError ? e.message : "알 수 없는 오류가 났어요." }]);
    } finally {
      setBusy(false);
    }
  }

  if (!isOpen) return null;
  return (
    <>
      <div className="absolute inset-0 z-40 bg-[linear-gradient(90deg,rgb(4_5_12/0.25)_30%,rgb(4_5_12/0.8))] backdrop-blur-[3px]" onClick={close} />
      <aside
        role="dialog"
        aria-label="멀티버스 로어 AI"
        className="glass absolute top-[28px] right-[48px] bottom-[28px] z-50 flex w-[600px] flex-col overflow-hidden rounded-[30px] border-white/20 bg-[linear-gradient(170deg,rgb(60_50_120/0.42),rgb(14_16_40/0.72)_45%,rgb(10_12_30/0.85))] shadow-[0_50px_120px_rgb(0_0_0/0.75),0_0_80px_rgb(139_92_246/0.25)]"
      >
        <header className="flex items-center gap-[18px] px-[30px] pt-[30px] pb-[22px]">
          <div className="orb relative size-[74px] flex-none" style={{ ["--t" as string]: "7s", ["--glow" as string]: "rgb(167 139 250 / .8)" }} />
          <div>
            <h2 className="text-[26px] font-extrabold tracking-tight">
              멀티버스 <span className="grad">로어 AI</span>
            </h2>
            <p className="mt-1 text-sm text-muted">위키 원문을 찾아 근거와 함께 답해요</p>
          </div>
          <button type="button" onClick={close} aria-label="닫기" className="ml-auto grid size-[42px] place-items-center rounded-full border border-line bg-white/5 hover:bg-white/10">
            <IconX size={18} />
          </button>
        </header>

        <div className="flex flex-wrap gap-2 border-b border-line px-[30px] pb-5">
          {work && (
            <button type="button" className="chip chip-on" onClick={() => setWork(null)} title="작품 맥락 빼기">
              {work.title} 에 대해 묻는 중 <IconX size={14} aria-label="빼기" />
            </button>
          )}
          {PROMPTS.map((p) => (
            <button key={p} type="button" className="chip h-[34px] text-sm hover:bg-white/10" onClick={() => ask(p)} disabled={busy}>
              <IconSparkles size={14} aria-hidden />
              {p}
            </button>
          ))}
        </div>

        <div className="scroll-area flex flex-1 flex-col gap-[18px] px-[30px] py-[22px]" aria-live="polite">
          {messages.length === 0 && (
            <p className="m-auto max-w-[400px] text-center leading-relaxed text-muted">
              MCU 영화 · 시리즈의 줄거리, 인물, 세계관을 물어보세요.
              <br />
              질문마다 따로 답해요 (앞 대화는 기억하지 않아요).
            </p>
          )}
          {messages.map((m, i) =>
            m.role === "me" ? (
              <div key={i} className="max-w-[78%] self-end rounded-[20px_20px_6px_20px] border border-[rgb(170_230_255/0.35)] bg-[linear-gradient(135deg,rgb(94_231_255/0.3),rgb(139_92_246/0.45))] px-[18px] py-3.5 whitespace-pre-wrap shadow-[0_10px_30px_rgb(0_0_0/0.4)]">
                {m.text}
              </div>
            ) : (
              <AiBubble key={i} msg={m} />
            ),
          )}
          {busy && (
            <div className="flex items-center gap-3 text-muted">
              <div className="orb relative size-[34px] flex-none" />
              자료를 찾고 답을 쓰는 중이에요… (10초쯤 걸려요)
            </div>
          )}
          <div ref={bottom} />
        </div>

        <form
          onSubmit={(e) => {
            e.preventDefault();
            ask(text);
          }}
          className="mx-6 mb-6 flex items-center gap-3 rounded-[20px] border border-line-hi bg-white/5 py-2 pr-2.5 pl-[22px] shadow-[0_0_30px_rgb(94_231_255/0.12)]"
        >
          <textarea
            ref={input}
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault();
                ask(text);
              }
            }}
            maxLength={500}
            rows={1}
            placeholder="우주의 비밀을 물어보세요…"
            aria-label="질문"
            className="max-h-32 flex-1 resize-none bg-transparent py-2.5 text-base outline-none placeholder:text-dim"
          />
          <button type="submit" disabled={busy || !text.trim()} aria-label="보내기" className="grid size-[46px] place-items-center rounded-[14px] bg-gradient-to-br from-cyan to-violet shadow-[0_0_24px_rgb(94_231_255/0.55)] disabled:opacity-40">
            <IconSend2 size={20} />
          </button>
        </form>
      </aside>
    </>
  );
}

// ---------- 답 한 건 ----------

function AiBubble({ msg }: { msg: Extract<Msg, { role: "ai" }> }) {
  const d = msg.data;
  const web = d?.status === "web_unverified";
  return (
    <div className="flex gap-3">
      <div className="orb relative size-[34px] flex-none [animation:none]" />
      <div className="flex-1 rounded-[6px_20px_20px_20px] border border-line bg-white/5 px-[18px] py-4 shadow-[0_14px_34px_rgb(0_0_0/0.45)]">
        {msg.error && (
          <p className="flex items-center gap-2 text-amber">
            <IconAlertTriangle size={18} aria-hidden />
            {msg.error}
          </p>
        )}
        {d && (
          <>
            {web && (
              <div className="mb-2.5 flex items-center gap-2 rounded-[10px] border border-amber/40 bg-amber/10 px-3 py-2 text-[13px] font-semibold text-amber">
                <IconAlertTriangle size={16} aria-hidden />웹 검색 결과 · 미검증
              </div>
            )}
            {d.search_query && d.search_query !== d.question && (
              <div className="mb-2.5 flex items-center gap-1.5 text-[13px] text-dim">
                <IconSearch size={14} aria-hidden />이렇게 찾았어요 <code className="font-mono text-[12.5px] text-[#b9c2ff]">{d.search_query}</code>
              </div>
            )}
            <p className="leading-[1.7] whitespace-pre-wrap text-[#e9ebff]">
              <AnswerText text={d.answer} web={web} />
            </p>
            {d.sources.map((s) => (
              <SourceCard key={s.n} s={s} />
            ))}
            <div className="mt-3 flex flex-wrap gap-2">
              {d.status === "answered" && (
                <span className="chip border-emerald-400/35 bg-emerald-400/10 text-emerald-200">
                  <IconCircleCheck size={14} aria-hidden />문서 근거
                </span>
              )}
              {web && (
                <span className="chip border-amber/40 bg-amber/10 text-amber">
                  <IconWorld size={14} aria-hidden />웹 미검증
                </span>
              )}
              {d.status === "no_evidence" && <span className="chip">근거 없음</span>}
              {d.partial && <span className="chip">일부만 확인됨</span>}
              {d.sources.length > 0 && <span className="chip">근거 {d.sources.length}개</span>}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

// 답 속 [1] · [1, 2] 를 출처 번호 표시로 바꾼다 (글은 텍스트로만 그린다)
function AnswerText({ text, web }: { text: string; web: boolean }) {
  const parts = text.split(/\[(\d+(?:,\s*\d+)*)\]/);
  return parts.map((part, i) =>
    i % 2 === 0 ? (
      <Fragment key={i}>{part}</Fragment>
    ) : (
      part.split(",").map((n) => <Cite key={`${i}-${n}`} n={n.trim()} web={web} />)
    ),
  );
}

function Cite({ n, web }: { n: string; web: boolean }) {
  return (
    <span className={`ml-0.5 inline-grid h-5 min-w-[22px] place-items-center rounded-md px-1 align-[2px] text-xs font-bold text-[#0b0820] ${web ? "bg-amber" : "bg-cyan"}`}>
      {web ? `w${n}` : n}
    </span>
  );
}

function SourceCard({ s }: { s: Source }) {
  const web = s.kind === "web_snippet";
  const name = [s.title_ko && s.title && s.title_ko !== s.title ? `${s.title_ko} (${s.title})` : s.title_ko || s.title, s.section_path]
    .filter(Boolean)
    .join(" · ");
  return (
    <a
      href={s.source_url}
      target="_blank"
      rel="noopener noreferrer"
      className="mt-3 block rounded-[14px] border border-line bg-bg/50 px-3.5 py-3 hover:border-line-hi"
    >
      <div className="flex items-center gap-2 text-sm font-semibold">
        <Cite n={String(s.n)} web={web} />
        <span className="line-clamp-1">{name || s.source_url}</span>
        <IconExternalLink size={14} className="ml-auto flex-none text-dim" aria-label="원문 열기" />
      </div>
      <blockquote className="mt-1.5 line-clamp-3 font-mono text-[12.5px] leading-relaxed text-muted">“{s.excerpt}”</blockquote>
      {s.license && <p className="mt-1 text-[11px] text-dim">{web ? "웹 검색 결과" : "Wikipedia"} · {s.license}</p>}
    </a>
  );
}
