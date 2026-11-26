// 세로로 긴 페이지(무대 높이 px). 여기 없는 페이지는 1920x1080 한 화면을 16:9 그대로 맞춘다
export const TALL: Record<string, number> = { "/": 1440, "/community/": 1540 };

// 무대 크기 맞추기. layout 의 <head> 스크립트(첫 화면 전)와 Stage(창 크기 · 페이지가 바뀔 때)가 같이 쓴다
export function fitStage(path: string, tall: Record<string, number> = TALL): void {
  const d = document.documentElement;
  const h = tall[path.endsWith("/") ? path : `${path}/`];
  const fit = Math.min(innerWidth / 1920, innerHeight / 1080);
  d.classList.toggle("scroll", !!h);
  d.style.setProperty("--h", `${h || 1080}px`);
  d.style.setProperty("--s", String(h ? (d.clientWidth || innerWidth) / 1920 : fit));
  d.style.setProperty("--fs", String(fit)); // 챗봇 층은 늘 한 화면에 맞춘다
}
