import { after, NextRequest, NextResponse } from 'next/server';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';
export const maxDuration = 800;

const apiOrigin = () => (process.env.API_ORIGIN || 'http://127.0.0.1:8000').replace(/\/$/, '');

async function proxy(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  // Internal worker routes are never exposed through the browser proxy.
  if (path[0] !== 'v1' || path[1] === 'internal' || path.some(p => p === '..' || p.includes('/'))) {
    return NextResponse.json({ code: 'NOT_FOUND', message: '요청한 경로를 찾을 수 없습니다.', retryable: false }, { status: 404 });
  }
  const headers = new Headers();
  for (const name of ['content-type', 'cookie', 'x-csrf-token', 'idempotency-key', 'accept']) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }
  const suppliedOrigin = request.headers.get('origin');
  if (suppliedOrigin) headers.set('origin', suppliedOrigin);
  const url = `${apiOrigin()}/${path.map(encodeURIComponent).join('/')}${request.nextUrl.search}`;
  try {
    const upstream = await fetch(url, {
      method: request.method,
      headers,
      body: ['GET', 'HEAD'].includes(request.method) ? undefined : await request.arrayBuffer(),
      redirect: 'manual',
      cache: 'no-store',
      signal: AbortSignal.timeout(50000),
    });
    const responseHeaders = new Headers({ 'cache-control': 'no-store, private' });
    for (const name of ['content-type', 'content-disposition', 'retry-after', 'location']) {
      const value = upstream.headers.get(name);
      if (value) responseHeaders.set(name, value);
    }
    for (const cookie of upstream.headers.getSetCookie()) responseHeaders.append('set-cookie', cookie);
    const startsExport = ['v1/exports', 'v1/jobs'].includes(path.join('/')) || (path[1] === 'jobs' && path[3] === 'retry');
    if (request.method === 'POST' && startsExport && upstream.ok && process.env.WORKER_SECRET) {
      after(async () => {
        try {
          await fetch(`${apiOrigin()}/v1/internal/jobs/process`, {
            method: 'POST',
            headers: { authorization: `Bearer ${process.env.WORKER_SECRET}`, 'content-type': 'application/json' },
            body: JSON.stringify({ limit: 1 }),
            signal: AbortSignal.timeout(750000),
          });
        } catch { console.error('Platform worker could not be reached; durable job remains queued.'); }
      });
    }
    return new Response(upstream.body, { status: upstream.status, headers: responseHeaders });
  } catch {
    return NextResponse.json({ code: 'API_UNAVAILABLE', message: '서버에 연결할 수 없습니다. 잠시 후 다시 시도해 주세요.', retryable: true }, { status: 503 });
  }
}

export { proxy as GET, proxy as POST, proxy as PATCH, proxy as PUT, proxy as DELETE };
