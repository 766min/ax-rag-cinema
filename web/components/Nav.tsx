"use client";

import { IconSearch, IconSparkles } from "@tabler/icons-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useChat } from "./ChatPanel";

const MENU = [
  { href: "/", label: "홈" },
  { href: "/explore/", label: "탐색" },
  { href: "/universe/", label: "세계관" },
  { href: "/community/", label: "커뮤니티" },
];

export default function Nav() {
  const path = usePathname();
  const router = useRouter();
  const chat = useChat();
  const active = (href: string) => (href === "/" ? path === "/" : path.startsWith(href.slice(0, -1)));

  return (
    <nav
      className="glass absolute top-[28px] right-[48px] left-[48px] z-30 flex h-[84px] items-center gap-[44px] pr-[18px] pl-[28px]"
      aria-label="주 메뉴"
    >
      <Link href="/" className="flex items-center gap-1.5" aria-label="CINEVERSE 홈">
        <span className="bg-[linear-gradient(90deg,#d8fbff,#5ee7ff_45%,#a78bfa)] bg-clip-text text-[34px] font-extrabold tracking-[0.06em] text-transparent drop-shadow-[0_0_16px_rgb(94_231_255/0.45)]">
          CINEVERSE
        </span>
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src="/bus.svg" alt="" className="h-[77px] w-[132px] animate-[float_6s_ease-in-out_infinite]" />
      </Link>

      <div className="flex gap-9 text-lg text-muted">
        {MENU.map((m) => (
          <Link
            key={m.href}
            href={m.href}
            aria-current={active(m.href) ? "page" : undefined}
            className="relative py-1.5 hover:text-ink aria-[current=page]:text-ink aria-[current=page]:after:absolute aria-[current=page]:after:right-0 aria-[current=page]:after:-bottom-1.5 aria-[current=page]:after:left-0 aria-[current=page]:after:h-0.5 aria-[current=page]:after:rounded aria-[current=page]:after:bg-gradient-to-r aria-[current=page]:after:from-cyan aria-[current=page]:after:to-violet aria-[current=page]:after:shadow-[0_0_12px_#5ee7ff] aria-[current=page]:after:content-['']"
          >
            {m.label}
          </Link>
        ))}
      </div>

      <div className="ml-auto flex items-center gap-3.5">
        <form
          role="search"
          onSubmit={(e) => {
            e.preventDefault();
            const q = new FormData(e.currentTarget).get("q")?.toString().trim() ?? "";
            router.push(`/explore/${q ? `?q=${encodeURIComponent(q)}` : ""}`);
          }}
          className="flex h-12 w-[300px] items-center gap-2.5 rounded-full border border-line bg-white/5 px-[18px] text-dim focus-within:border-cyan"
        >
          <IconSearch size={18} aria-hidden />
          <input
            name="q"
            placeholder="작품 검색"
            aria-label="작품 검색"
            className="w-full bg-transparent text-[15px] text-ink outline-none placeholder:text-dim"
          />
        </form>
        <button
          type="button"
          onClick={() => chat.open()}
          aria-label="로어 AI 열기"
          className="grid size-12 place-items-center rounded-full border border-line bg-white/5 text-cyan hover:bg-white/10"
        >
          <IconSparkles size={22} />
        </button>
      </div>
    </nav>
  );
}
