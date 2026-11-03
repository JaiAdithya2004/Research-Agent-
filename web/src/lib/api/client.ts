import createClient from "openapi-fetch";
import type { paths } from "./schema.gen";

/**
 * Typed client over the generated schema. The browser only ever talks to the BFF proxy at /api;
 * the proxy adds the API key. `fetchImpl` is injectable for tests.
 */
export function createApi(baseUrl: string, fetchImpl?: typeof fetch) {
  return createClient<paths>({ baseUrl, ...(fetchImpl ? { fetch: fetchImpl } : {}) });
}

export type Api = ReturnType<typeof createApi>;

export function browserBaseUrl(): string {
  return typeof window === "undefined" ? "http://127.0.0.1:3000/api" : `${window.location.origin}/api`;
}

let shared: Api | undefined;
/** The client used by hooks in the browser. */
export function getApi(): Api {
  shared ??= createApi(browserBaseUrl());
  return shared;
}
