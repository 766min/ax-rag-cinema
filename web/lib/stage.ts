// 세로로 긴 페이지(무대 높이 px). 여기 없는 페이지는 1920x1080 한 화면을 16:9 그대로 맞춘다
export const TALL: Record<string, number> = { "/": 1440, "/community/": 1540 };

// 무대 크기 맞추기. layout 의 <head> 스크립트(첫 화면 전)와 Stage(창 크기 · 페이지 · 챗봇이 바뀔 때)가 같이 쓴다.
// 챗봇이 열리면(html data-chat="open") 크롬의 제미나이 패널처럼 오른쪽에 붙고, 페이지는 남은 폭에 맞춰 줄어든다
export function fitStage(path: string, tall: Record<string, number> = TALL): void {
  const d = document.documentElement;
  const h = tall[path.endsWith("/") ? path : `${path}/`];
  const narrow = innerWidth < 900; // 좁은 창에서는 챗봇이 화면 전체를 덮는다
  const chat = d.dataset.chat === "open" ? (narrow ? innerWidth : Math.round(Math.min(560, Math.max(400, innerWidth * 0.32)))) : 0;
  const cut = narrow ? 0 : chat; // 페이지에서 빼는 폭
  const width = (h ? d.clientWidth || innerWidth : innerWidth) - cut;
  d.classList.toggle("scroll", !!h);
  d.style.setProperty("--h", `${h || 1080}px`);
  d.style.setProperty("--chat-w", `${chat}px`);
  d.style.setProperty("--page-cut", `${cut}px`);
  d.style.setProperty("--s", String(h ? width / 1920 : Math.min(width / 1920, innerHeight / 1080)));
}
