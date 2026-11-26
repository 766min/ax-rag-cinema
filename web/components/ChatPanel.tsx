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

  const panel = useRef<HTMLElement>(null);

  // 열 때 입력창으로, 닫을 때 열기 전에 누르던 버튼으로 포커스를 돌려준다
  useEffect(() => {
    if (!isOpen) return;
    const opener = document.activeElement as HTMLElement | null;
    input.current?.focus();
    return () => opener?.focus();
  }, [isOpen]);
  useEffect(() => {
    bottom.current?.scrollIntoView({ block: "end" }); // 요즘 브라우저는 Promise 를 돌려줘서 정리 함수로 넘기면 안 된다
  }, [messages, busy]);
  useEffect(() => {
    const esc = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
      if (e.key !== "Tab" || !panel.current) return;
      // 포커스 가두기: Tab 이 창 밖(뒤쪽 화면)으로 나가지 않고 창 안에서 돈다
      const items = [...panel.current.querySelectorAll<HTMLElement>("a[href], button:not([disabled]), textarea, input")];
      const first = items[0];
      const last = items.at(-1);
      const inside = panel.current.contains(document.activeElement);
      if (e.shiftKey && (document.activeElement === first || !inside)) {
        e.preventDefault();
        last?.focus();
      } else if (!e.shiftKey && (document.activeElement === last || !inside)) {
        e.preventDefault();
        first?.focus();
      }
    };
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
      <div className="absolute inset-0 z-40 bg-[linear-gradient(90deg,rgb(7_7_11/0.05)_35%,rgb(7_7_11/0.7))] backdrop-blur-[4px] backdrop-brightness-[0.55]" onClick={close} />
      <div className="glow z-40" style={{ left: 1240, top: 140, width: 520, height: 420, background: "#6a4fd6" }} />
      <div className="glow z-40" style={{ left: 1500, top: 600, width: 460, height: 380, background: "#2f7fd8" }} />
      <aside
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-label="로어 AI 그루트"
        className="frost absolute top-[28px] right-[48px] bottom-[28px] z-50 flex w-[600px] flex-col overflow-hidden rounded-[28px] bg-white/6 backdrop-blur-[26px] shadow-[inset_0_1px_0_rgb(255_255_255/0.16),0_40px_100px_rgb(0_0_0/0.6)]"
      >
        <header className="flex items-center gap-4 px-[30px] pt-[26px] pb-5">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src="/groot.png" alt="" className="size-14 flex-none rounded-full border border-white/24 bg-[#16161d] object-cover object-[50%_8%]" />
          <div>
            <div className="text-[12.5px] text-low">홈 › 로어 AI</div>
            <h2 className="mt-0.5 text-[22px] font-bold tracking-tight text-hi">
              로어 AI <span className="grad">그루트</span>
              <span className="ml-2 inline-flex items-center gap-[5px] align-[3px] text-xs font-medium text-[#9be7b8] before:size-1.5 before:rounded-full before:bg-[#4fd68a] before:content-['']">
                답변 가능
              </span>
            </h2>
            <p className="mt-0.5 text-[13.5px] text-mid">위키 원문을 찾아 근거와 함께 답해요</p>
          </div>
          <button type="button" onClick={close} aria-label="닫기" className="icon-btn ml-auto size-10">
            <IconX size={18} />
          </button>
        </header>

        <div className="flex flex-wrap gap-2 border-b border-white/8 px-[30px] pb-5">
          {work && (
            <button type="button" className="chip chip-on" onClick={() => setWork(null)} title="작품 맥락 빼기">
              {work.title} 에 대해 묻는 중 <IconX size={14} aria-label="빼기" />
            </button>
          )}
          {PROMPTS.map((p) => (
            <button key={p} type="button" className="chip h-8 px-[13px] text-mid transition-colors hover:bg-white/10 hover:text-hi" onClick={() => ask(p)} disabled={busy}>
              <IconSparkles size={14} aria-hidden />
              {p}
            </button>
          ))}
        </div>

        <div className="scroll-area flex flex-1 flex-col gap-[22px] px-[30px] py-[26px]" aria-live="polite">
          {messages.length > 0 && <div className="self-center text-xs text-low">오늘</div>}
          {messages.length === 0 && (
            <p className="m-auto max-w-[400px] text-center leading-relaxed text-mid">
              MCU 영화 · 시리즈의 줄거리, 인물, 세계관을 물어보세요.
              <br />
              질문마다 따로 답해요 (앞 대화는 기억하지 않아요).
            </p>
          )}
          {messages.map((m, i) =>
            m.role === "me" ? (
              <div key={i} className="max-w-[78%] self-end rounded-[20px_20px_6px_20px] bg-hi px-4 py-3 text-[15.5px] leading-normal whitespace-pre-wrap text-[#111] shadow-[0_8px_22px_rgb(0_0_0/0.35)]">
                {m.text}
              </div>
            ) : (
              <AiBubble key={i} msg={m} />
            ),
          )}
          {busy && (
            <div className="flex items-center gap-3 text-mid">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src="/groot.png" alt="" className="size-[34px] animate-[bob_1.8s_ease-in-out_infinite] flex-none rounded-full border border-white/24 bg-[#16161d] object-cover object-[50%_8%]" />
              자료를 찾고 답을 쓰는 중이에요… (10~20초쯤 걸려요)
            </div>
          )}
          <div ref={bottom} />
        </div>

        <form
          onSubmit={(e) => {
            e.preventDefault();
            ask(text);
          }}
          className="mx-[22px] mb-[22px] flex min-h-[60px] items-center gap-3 rounded-[30px] border border-white/12 bg-white/7 py-2 pr-2 pl-[22px] shadow-[inset_0_1px_0_rgb(255_255_255/0.1)] transition-colors focus-within:border-white/24 hover:border-white/24"
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
            className="max-h-32 flex-1 resize-none bg-transparent py-2.5 text-[15px] outline-none placeholder:text-low"
          />
          <button type="submit" disabled={busy || !text.trim()} aria-label="보내기" className="grid size-11 flex-none place-items-center rounded-full bg-hi text-[#111] transition-transform hover:scale-105 disabled:opacity-40">
            <IconSend2 size={19} />
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
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src="/groot.png" alt="" className="size-[34px] flex-none rounded-full border border-white/24 bg-[#16161d] object-cover object-[50%_8%]" />
      <div className="flex-1 rounded-[6px_20px_20px_20px] border border-white/12 bg-white/6 px-[18px] py-4 shadow-[inset_0_1px_0_rgb(255_255_255/0.1)]">
        {msg.error && (
          <p className="flex items-center gap-2 text-amber">
            <IconAlertTriangle size={18} aria-hidden />
            {msg.error}
          </p>
        )}
        {d && (
          <>
            {web && (
              <div className="mb-2.5 flex items-center gap-2 rounded-[10px] border border-amber/30 bg-amber/10 px-3 py-2 text-[12.5px] font-semibold text-[#ffd9a3]">
                <IconAlertTriangle size={16} aria-hidden />웹 검색 결과 · 미검증
              </div>
            )}
            {d.search_query && d.search_query !== d.question && (
              <div className="mb-2.5 flex items-center gap-1.5 text-[12.5px] text-low">
                <IconSearch size={14} aria-hidden />이렇게 찾았어요 <code className="rounded-[5px] bg-white/6 px-1.5 py-0.5 font-mono text-xs text-mid">{d.search_query}</code>
              </div>
            )}
            <p className="text-[15.5px] leading-[1.7] whitespace-pre-wrap text-hi">
              <AnswerText text={d.answer} web={web} />
            </p>
            {d.sources.map((s) => (
              <SourceCard key={s.n} s={s} />
            ))}
            <div className="mt-3 flex flex-wrap gap-2">
              {d.status === "answered" && (
                <span className="chip h-[26px] border-[rgb(79_214_138/0.35)] bg-[rgb(79_214_138/0.12)] px-2.5 text-xs text-[#a7f0c6]">
                  <IconCircleCheck size={14} aria-hidden />문서 근거
                </span>
              )}
              {web && (
                <span className="chip h-[26px] border-amber/40 bg-amber/12 px-2.5 text-xs text-[#ffd9a3]">
                  <IconWorld size={14} aria-hidden />웹 미검증
                </span>
              )}
              {d.status === "no_evidence" && <span className="chip h-[26px] px-2.5 text-xs">근거 없음</span>}
              {d.partial && <span className="chip h-[26px] px-2.5 text-xs">일부만 확인됨</span>}
              {d.sources.length > 0 && <span className="chip h-[26px] px-2.5 text-xs">근거 {d.sources.length}개</span>}
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
    <span className={`ml-[3px] inline-grid h-[19px] min-w-5 place-items-center rounded-md px-[5px] align-[2px] text-[11.5px] font-bold text-[#111] ${web ? "bg-amber" : "bg-hi"}`}>
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
      className="mt-3 block rounded-[14px] border border-white/8 bg-black/22 px-3.5 py-3 transition-colors hover:border-white/24"
    >
      <div className="flex items-center gap-2 text-[13.5px] font-semibold text-hi">
        <Cite n={String(s.n)} web={web} />
        <span className="line-clamp-1">{name || s.source_url}</span>
        <IconExternalLink size={14} className="ml-auto flex-none text-low" aria-label="원문 열기" />
      </div>
      <blockquote className="mt-1.5 line-clamp-3 font-mono text-xs leading-relaxed text-mid">“{s.excerpt}”</blockquote>
      {s.license && <p className="mt-1 text-[11px] text-low">{web ? "웹 검색 결과" : "Wikipedia"} · {s.license}</p>}
    </a>
  );
}
