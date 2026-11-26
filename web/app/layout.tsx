import type { Metadata } from "next";
import "./globals.css";
import ChatPanel, { ChatProvider } from "@/components/ChatPanel";
import Nav from "@/components/Nav";
import Stage from "@/components/Stage";

export const metadata: Metadata = {
  title: "CINEVERSE · 시네버스",
  description: "MCU 영화와 시리즈의 줄거리 · 인물 · 세계관을 위키 원문 근거와 함께 찾아 주는 로어 AI",
};

// 첫 화면이 그려지기 전에 16:9 무대 크기를 맞춘다 (Stage 가 창 크기가 바뀔 때 다시 맞춘다)
const FIT = `document.documentElement.style.setProperty("--s",Math.min(innerWidth/1920,innerHeight/1080))`;

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    // FIT 스크립트가 그리기 전에 style(--s)을 넣으므로 이 태그의 속성 차이는 정상이다
    <html lang="ko" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: FIT }} />
      </head>
      <body>
        <ChatProvider>
          <Stage>
            {children}
            <Nav />
            <ChatPanel />
          </Stage>
        </ChatProvider>
      </body>
    </html>
  );
}
