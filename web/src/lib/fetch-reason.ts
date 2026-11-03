/** Friendly labels for the coarse `fetch_reason` codes. Unknown codes fall back to the raw value. */
const REASONS: Record<string, string> = {
  ok: "Page read",
  http_denied: "The site refused access (HTTP 401 or 403)",
  http_not_found: "The page was not found (HTTP 404 or 410)",
  http_rate_limited: "The site rate-limited us (HTTP 429)",
  http_server_error: "The site had a server error (HTTP 5xx)",
  http_payment_required: "The page needs payment (HTTP 402)",
  http_other: "The site answered with an unexpected HTTP status",
  timeout: "The site took too long to answer",
  connect_error: "Could not connect to the site",
  network_error: "A network error stopped the request",
  too_many_redirects: "Too many redirects",
  redirect_invalid: "A redirect pointed somewhere not allowed",
  robots_disallowed: "The site's robots.txt does not allow fetching this page",
  robots_unavailable: "The site's robots.txt could not be read",
  blocked_by_policy: "Not fetched: the address is blocked by our network safety policy",
  unsupported_content_type: "Not a text page (unsupported content type)",
  unsupported_encoding: "Unsupported content encoding",
  bad_encoding: "The text could not be decoded",
  too_large: "The page is larger than the 1 MB limit",
  html_too_complex: "The page is too complex to read safely",
  paywall_marker: "The page looks paywalled",
  js_or_empty: "The page is empty without JavaScript",
  internal_error: "Our fetcher hit an internal error",
};

export function fetchReasonLabel(code: string): string {
  return REASONS[code] ?? code;
}
