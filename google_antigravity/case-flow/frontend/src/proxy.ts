import { NextResponse, type NextRequest } from 'next/server';

// 서버 단계 로그인 게이트: 세션 쿠키(Django sessionid)가 없으면 페이지를 그리기 전에
// 곧바로 /login으로 보낸다 — 메인 화면이 잠깐 떴다가 리다이렉션되는 것을 방지.
// 쿠키는 백엔드(:8000)가 심지만 host 기준이라 :3000 요청에도 같이 실려 온다.
// 쿠키가 있어도 만료됐을 수 있으므로 실제 검증은 AuthGate(/api/auth/me/)가 계속 담당한다.
// (쿠키 존재 시 /login→메인 강제 이동은 만료 세션에서 무한 루프가 되므로 하지 않음)
export function proxy(request: NextRequest) {
  const { pathname } = request.nextUrl;
  const hasSession = request.cookies.has('sessionid');
  if (!hasSession && !pathname.startsWith('/login')) {
    const url = request.nextUrl.clone();
    url.pathname = '/login';
    return NextResponse.redirect(url);
  }
  return NextResponse.next();
}

export const config = {
  // 정적 리소스는 게이트 대상에서 제외
  matcher: ['/((?!_next/static|_next/image|favicon.ico).*)'],
};
