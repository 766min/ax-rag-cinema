// FastAPI(/api/*) 호출 한 곳. 응답은 팀 공통 APIResponse 모양 { success, code, message, data }
// 개발: next dev(3000) → uvicorn(8000) / 배포: FastAPI 가 화면을 같이 보내서 같은 주소("")
const BASE =
  process.env.NEXT_PUBLIC_API_BASE ??
  (process.env.NODE_ENV === "development" ? "http://localhost:8000" : "");

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
    public code: string | null,
  ) {
    super(message);
  }
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${BASE}/api${path}`, {
      ...init,
      headers: init?.body ? { "Content-Type": "application/json", ...init.headers } : init?.headers,
    });
  } catch {
    throw new ApiError("서버에 연결하지 못했어요. 잠시 후 다시 시도해 주세요.", 0, null);
  }
  const body = await res.json().catch(() => null);
  if (!res.ok || !body?.success) {
    const message =
      res.status === 422 ? "입력한 내용을 다시 확인해 주세요." : body?.message ?? "요청을 처리하지 못했어요.";
    throw new ApiError(message, res.status, body?.code ?? null);
  }
  return body.data as T;
}

export const post = (data: unknown, method = "POST") =>
  ({ method, body: JSON.stringify(data) }) satisfies RequestInit;

// ---------- 영화 ----------

export type Work = {
  work_id: string;
  title_ko: string;
  title_original: string;
  format: string;
  year: number | null;
  phase: string | null;
  timeline_order: number | null;
  is_canon: boolean | null;
};

export type WorkDetail = Work & { summary: string; source_url: string | null; license: string | null };

const FORMAT_KO: Record<string, string> = {
  movie: "영화",
  tvSeries: "시리즈",
  tvMiniSeries: "미니시리즈",
  tvSpecial: "스페셜",
  tvMovie: "TV 영화",
  tvEpisode: "에피소드",
  short: "단편",
  tvShort: "단편",
  video: "비디오",
};
export const formatKo = (format: string) => FORMAT_KO[format] ?? format;
export const workMeta = (w: Work) => [w.year, formatKo(w.format)].filter(Boolean).join(" · ");
export const workHref = (w: Pick<Work, "work_id">) => `/movie/?id=${encodeURIComponent(w.work_id)}`;

// ---------- 챗봇 ----------

export type AnswerStatus = "answered" | "no_evidence" | "web_unverified" | "retrieval_error" | "generation_error";

export type Source = {
  n: number;
  chunk_id: string;
  kind: "document" | "web_snippet";
  title: string | null;
  title_ko: string | null;
  section_path: string | null;
  excerpt: string;
  source_url: string;
  license: string | null;
};

export type AskData = {
  request_id: string;
  question: string;
  answer: string;
  status: AnswerStatus;
  search_query: string;
  partial: boolean;
  sources: Source[];
};

// ---------- 게시판 ----------

export const CATEGORIES = {
  lore: "세계관 로어",
  post_credits: "쿠키 영상",
  watch_order: "시청 순서",
  theory: "팬 이론",
  free: "자유",
} as const;
export type Category = keyof typeof CATEGORIES;

export type Post = {
  id: number;
  category: Category;
  title: string;
  body: string;
  nickname: string;
  work_id: string | null;
  like_count: number;
  comment_count: number;
  created_at: string;
};
export type Comment = { id: number; post_id: number; nickname: string; body: string; created_at: string };
export type PostDetail = Post & { comments: Comment[] };
export type PostPage = { items: Post[]; page: number; has_more: boolean };

export function ago(iso: string): string {
  const min = Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 60000));
  if (min < 1) return "방금";
  if (min < 60) return `${min}분 전`;
  if (min < 60 * 24) return `${Math.floor(min / 60)}시간 전`;
  if (min < 60 * 24 * 2) return "어제";
  return new Date(iso).toLocaleDateString("ko-KR");
}
