import createClient from "openapi-fetch";
import type { paths } from "./schema.gen";

/**
 * Typed client over the generated schema. The browser only ever talks to the BFF proxy at /api;
 * the proxy adds the API key. `fetchImpl` is injectable for tests.
 */
export function createApi(baseUrl: string, fetchImpl?: typeof fetch) {
  // Late-bound global fetch, so tests can stub fetch after the shared client was created.
  const lateFetch = (input: Request) => globalThis.fetch(input);
  const send = fetchImpl ?? lateFetch;
  // A request is aborted when the page cancels it (navigating away, a newer query replacing it).
  // Nobody is waiting for the answer any more, so leave the call pending instead of rejecting:
  // a rejection nobody handles shows up as a "signal is aborted without reason" error overlay.
  const quietAbort = async (input: Request): Promise<Response> => {
    try {
      const response = await send(input);
      // The cancel can also land while the body is being read, which rejects the same way.
      for (const name of ["json", "text", "blob", "arrayBuffer", "formData"] as const) {
        const read = response[name].bind(response) as () => Promise<unknown>;
        Object.defineProperty(response, name, {
          value: async () => {
            try {
              return await read();
            } catch (error) {
              if (input.signal.aborted) return new Promise<never>(() => {});
              throw error;
            }
          },
        });
      }
      return response;
    } catch (error) {
      if (input.signal.aborted) return new Promise<Response>(() => {});
      throw error;
    }
  };
  return createClient<paths>({ baseUrl, fetch: quietAbort });
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
