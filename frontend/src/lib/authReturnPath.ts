/** Keep a login return destination inside this application. */
export function authReturnPath(value: unknown, fallback = '/script-center'): string {
  if (typeof value !== 'string' || !value.startsWith('/') || value.startsWith('//')) return fallback;
  if (/[\\\u0000-\u0020\u007f]/.test(value) || /%(?:2f|5c|0[0-9a-f]|1[0-9a-f]|7f|25)/i.test(value)) return fallback;
  try {
    const url = new URL(value, 'https://local.invalid');
    const pathname = decodeURIComponent(url.pathname);
    if (url.origin !== 'https://local.invalid' || pathname.startsWith('//') || /^\/auth(?:\/|$)/i.test(pathname)) return fallback;
    return url.pathname + url.search + url.hash;
  } catch {
    return fallback;
  }
}
