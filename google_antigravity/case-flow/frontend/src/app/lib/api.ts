// 백엔드 API 주소. NEXT_PUBLIC_API_BASE가 있으면 그 값을 쓰고,
// 없으면 접속한 호스트(localhost든 사내망 IP든)의 8000 포트로 자동 연결한다.
// ||를 쓰는 이유: 도커 빌드에서 ARG 미지정 시 빈 문자열이 박히는데,
// ??는 빈 문자열을 유효한 값으로 취급해 자동 추론이 무시된다.
export function apiUrl(path: string): string {
  const base =
    process.env.NEXT_PUBLIC_API_BASE ||
    (typeof window !== 'undefined'
      ? `http://${window.location.hostname}:8000`
      : 'http://localhost:8000');
  return `${base}${path}`;
}

function getCookie(name: string): string | null {
  if (typeof document === 'undefined') return null;
  const match = document.cookie.match(new RegExp('(^|; )' + name + '=([^;]*)'));
  return match ? decodeURIComponent(match[2]) : null;
}

// 세션 쿠키를 포함하고, 쓰기 요청에는 CSRF 토큰을 자동으로 붙이는 공용 fetch.
// 인증이 만료되면(401/403) 로그인 페이지로 이동한다.
export async function apiFetch(path: string, options: RequestInit = {}): Promise<Response> {
  const headers = new Headers(options.headers);
  const method = (options.method || 'GET').toUpperCase();
  if (method !== 'GET' && method !== 'HEAD') {
    const token = getCookie('csrftoken');
    if (token) headers.set('X-CSRFToken', token);
  }
  const response = await fetch(apiUrl(path), { ...options, headers, credentials: 'include' });
  if (
    (response.status === 401 || response.status === 403) &&
    typeof window !== 'undefined' &&
    !window.location.pathname.startsWith('/login')
  ) {
    window.location.href = '/login';
  }
  return response;
}
