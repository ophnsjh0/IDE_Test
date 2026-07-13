'use client';

import { useEffect, useState } from 'react';
import { usePathname } from 'next/navigation';
import { Center, Loader } from '@mantine/core';
import { apiFetch } from '../lib/api';

// 로그인 없이 접근 가능한 경로
const PUBLIC_PATHS = ['/login'];

// 인증 확인이 끝나기 전에는 페이지를 렌더링하지 않는 게이트.
// 미인증 상태로 접속하면 케이스 목록이 잠깐 보였다가 /login으로 튕기는 대신
// 로더만 보이다가 바로 로그인 화면으로 이동한다 (이동은 apiFetch의 401/403 처리).
export default function AuthGate({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const isPublic = PUBLIC_PATHS.some((p) => pathname.startsWith(p));
  const [ready, setReady] = useState(false);

  useEffect(() => {
    if (isPublic || ready) return;
    apiFetch('/api/auth/me/')
      .then((res) => {
        if (res.ok) setReady(true);
      })
      .catch(() => {
        // 백엔드 연결 불가 — 화면을 열어 AppHeader의 연결 오류 표시가 보이게 한다
        setReady(true);
      });
  }, [isPublic, ready]);

  if (!isPublic && !ready) {
    return (
      <Center h="100vh">
        <Loader size="sm" />
      </Center>
    );
  }
  return <>{children}</>;
}
