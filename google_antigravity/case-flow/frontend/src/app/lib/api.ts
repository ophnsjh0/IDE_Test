// 백엔드 API 주소. NEXT_PUBLIC_API_BASE가 있으면 그 값을 쓰고,
// 없으면 접속한 호스트(localhost든 사내망 IP든)의 8000 포트로 자동 연결한다.
export function apiUrl(path: string): string {
  const base =
    process.env.NEXT_PUBLIC_API_BASE ??
    (typeof window !== 'undefined'
      ? `http://${window.location.hostname}:8000`
      : 'http://localhost:8000');
  return `${base}${path}`;
}
