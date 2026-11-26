import type { Metadata } from "next";
import { Anton } from "next/font/google";
import "./globals.css";
import ChatPanel, { ChatProvider } from "@/components/ChatPanel";
import Nav from "@/components/Nav";
import Stage from "@/components/Stage";
import { fitStage, TALL } from "@/lib/stage";

// 게시판 배너 제목(MULTIVERSE ARENA)용 글꼴. 빌드 때 받아 정적 파일로 같이 나간다
const anton = Anton({ weight: "400", subsets: ["latin"], variable: "--font-anton" });

export const metadata: Metadata = {
  title: "CINEVERSE · 시네버스",
  description: "MCU 영화와 시리즈의 줄거리 · 인물 · 세계관을 위키 원문 근거와 함께 찾아 주는 로어 AI",
  icons: { icon: "/logo-bus.png" },
};

// 첫 화면이 그려지기 전에 무대 크기를 맞춘다 (Stage 가 창 크기 · 페이지가 바뀔 때 다시 맞춘다)
const FIT = `(${fitStage.toString()})(location.pathname, ${JSON.stringify(TALL)})`;

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    // FIT 스크립트가 그리기 전에 class · style 을 넣으므로 이 태그의 속성 차이는 정상이다
    <html lang="ko" className={anton.variable} suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: FIT }} />
      </head>
      <body>
        <ChatProvider>
          <Stage>
            {children}
            <Nav />
          </Stage>
          {/* 챗봇은 오른쪽에 붙는 패널 (스크롤과 상관없이 화면 높이 그대로) */}
          <ChatPanel />
        </ChatProvider>
      </body>
    </html>
  );
}
