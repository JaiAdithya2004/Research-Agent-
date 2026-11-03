import { vi } from "vitest";

/** Shared state behind the mocked next/navigation. Tests set `nav.search` and read `nav.push`. */
export const nav = {
  pathname: "/",
  search: "",
  push: vi.fn(),
  replace: vi.fn(),
};

export function resetNav(): void {
  nav.pathname = "/";
  nav.search = "";
  nav.push.mockReset();
  nav.replace.mockReset();
}

export const navigationMock = {
  usePathname: () => nav.pathname,
  useSearchParams: () => new URLSearchParams(nav.search),
  useRouter: () => ({ push: nav.push, replace: nav.replace, back: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }),
};
