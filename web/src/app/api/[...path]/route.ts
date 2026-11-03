import { proxyRequest } from "@/server/proxy";

// Never cache or statically render: every request is forwarded live (SSE included).
export const dynamic = "force-dynamic";
export const runtime = "nodejs";

type Ctx = { params: Promise<{ path: string[] }> };

async function handler(request: Request, ctx: Ctx): Promise<Response> {
  const { path } = await ctx.params;
  return proxyRequest(request, path);
}

export const GET = handler;
export const POST = handler;
