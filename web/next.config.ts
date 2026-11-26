import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // npm run build → out/ 에 HTML · JS 만 남긴다. 배포에서는 FastAPI 가 이 폴더를 같이 보낸다
  output: "export",
  trailingSlash: true, // /community → /community/index.html (FastAPI 정적 파일과 경로를 맞춘다)
  images: { unoptimized: true },
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
