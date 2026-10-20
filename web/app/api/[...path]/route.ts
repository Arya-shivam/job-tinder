export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const privateHeaders = { "Cache-Control": "private, no-store" };

async function proxy(request: Request, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  if (path.some((part) => !part || part === "." || part === ".." || /[/\\]/.test(part))) {
    return Response.json({ detail: "Invalid API path." }, { status: 400, headers: privateHeaders });
  }

  try {
    const upstreamUrl = new URL(process.env.API_BASE_URL || "http://127.0.0.1:8000");
    upstreamUrl.pathname = `${upstreamUrl.pathname.replace(/\/+$/, "")}/api/${path.map(encodeURIComponent).join("/")}`;
    upstreamUrl.search = new URL(request.url).search;

    const headers = new Headers();
    for (const name of ["Accept", "Content-Type", "Cookie", "Origin", "Sec-Fetch-Site"]) {
      const value = request.headers.get(name);
      if (value !== null) headers.set(name, value);
    }

    const body = request.body;
    const init: RequestInit & { duplex?: "half" } = {
      method: request.method,
      headers,
      cache: "no-store",
      redirect: "error",
      signal: request.signal,
      ...(body ? { body, duplex: "half" as const } : {}),
    };
    const upstream = await fetch(upstreamUrl, init);
    const responseHeaders = new Headers(privateHeaders);
    for (const name of ["Content-Type", "Content-Disposition", "Retry-After"]) {
      const value = upstream.headers.get(name);
      if (value !== null) responseHeaders.set(name, value);
    }
    for (const cookie of upstream.headers.getSetCookie()) {
      responseHeaders.append("Set-Cookie", cookie);
    }
    return new Response(upstream.body, { status: upstream.status, headers: responseHeaders });
  } catch {
    return Response.json(
      { detail: "Job Match couldn’t reach its service. Try again in a moment." },
      { status: 502, headers: privateHeaders },
    );
  }
}

export { proxy as GET, proxy as POST, proxy as PATCH, proxy as DELETE };
