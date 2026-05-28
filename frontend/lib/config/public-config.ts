type PublicConfig = {
  apiBase: string;
};

const DEFAULT_API_BASE = "http://localhost:8080/api";
const SCRIPT_ID = "__shanpu_config__";

let browserCache: PublicConfig | null = null;

export function readPublicConfig(): PublicConfig {
  if (typeof window === "undefined") {
    const apiBase =
      process.env.INTERNAL_API_BASE_URL ??
      process.env.PUBLIC_API_BASE_URL ??
      process.env.NEXT_PUBLIC_API_BASE_URL ??
      DEFAULT_API_BASE;
    return { apiBase };
  }

  if (browserCache) return browserCache;

  const el = document.getElementById(SCRIPT_ID);
  if (el?.textContent) {
    try {
      const parsed = JSON.parse(el.textContent) as Partial<PublicConfig>;
      if (parsed.apiBase) {
        browserCache = { apiBase: parsed.apiBase };
        return browserCache;
      }
    } catch {
      // fall through to env var fallback
    }
  }

  const apiBase =
    process.env.NEXT_PUBLIC_API_BASE_URL ?? DEFAULT_API_BASE;
  browserCache = { apiBase };
  return browserCache;
}
