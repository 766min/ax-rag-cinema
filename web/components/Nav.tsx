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
      className="absolute top-6 right-10 left-10 z-30 flex h-[76px] items-center rounded-[18px] border border-white/8 bg-[linear-gradient(180deg,rgb(22_25_37/0.84),rgb(12_14_22/0.8))] pr-4 pl-6 shadow-[0_18px_50px_rgb(0_0_0/0.55),inset_0_1px_0_rgb(255_255_255/0.06)] backdrop-blur-[18px]"
      aria-label="주 메뉴"
    >
      <Link href="/" className="mr-5 flex h-10 items-center gap-2.5 border-r border-white/8 pr-7" aria-label="CINEVERSE 홈">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src="/logo-bus.png" alt="" className="h-[42px]" />
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src="/logo-word.png" alt="CINEVERSE" className="h-[26px]" />
      </Link>

      <div className="flex gap-1 text-[15.5px] font-medium text-[#9aa1b8]">
        {MENU.map((m) => (
          <Link
            key={m.href}
            href={m.href}
            aria-current={active(m.href) ? "page" : undefined}
            className="rounded-[10px] px-4 py-[9px] transition-colors hover:bg-white/4 hover:text-ink focus-visible:outline-2 focus-visible:outline-[#9fc2ff] aria-[current=page]:bg-white/8 aria-[current=page]:text-white aria-[current=page]:shadow-[inset_0_1px_0_rgb(255_255_255/0.06)]"
          >
            {m.label}
          </Link>
        ))}
      </div>

      <div className="ml-auto flex items-center gap-2.5">
        <form
          role="search"
          onSubmit={(e) => {
            e.preventDefault();
            const q = new FormData(e.currentTarget).get("q")?.toString().trim() ?? "";
            router.push(`/explore/${q ? `?q=${encodeURIComponent(q)}` : ""}`);
          }}
          className="flex h-11 w-[300px] items-center gap-2.5 rounded-xl border border-white/8 bg-white/4 px-3.5 text-dim focus-within:border-[#9fc2ff]"
        >
          <IconSearch size={18} aria-hidden />
          <input
            name="q"
            placeholder="작품 검색"
            aria-label="작품 검색"
            className="w-full bg-transparent text-sm text-ink outline-none placeholder:text-dim"
          />
        </form>
        <button type="button" onClick={() => chat.open()} aria-label="로어 AI 열기" className="icon-btn">
          <IconSparkles size={20} />
        </button>
      </div>
    </nav>
  );
}
