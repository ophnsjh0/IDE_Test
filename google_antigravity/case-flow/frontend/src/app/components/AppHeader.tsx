'use client';

import Link from 'next/link';
import { useEffect, useState } from 'react';
import { usePathname, useRouter } from 'next/navigation';
import { Group, Title, Button, Text } from '@mantine/core';
import {
  IconBook2,
  IconLayoutDashboard,
  IconList,
  IconLogout,
  IconUserCircle,
  IconUsers,
} from '@tabler/icons-react';
import { apiFetch } from '../lib/api';
import HelpAgentWidget from './HelpAgentDrawer';

export default function AppHeader() {
  const pathname = usePathname();
  const router = useRouter();
  const [username, setUsername] = useState('');
  const [isAdmin, setIsAdmin] = useState(false);
  const [isEngineer, setIsEngineer] = useState(false);
  const [connError, setConnError] = useState(false);

  // 로그인 상태 확인 겸 csrftoken 쿠키 발급. 미로그인이면 로그인 페이지로,
  // 백엔드에 연결 자체가 안 되면 헤더에 오류를 표시한다 (조용히 삼키지 않음).
  useEffect(() => {
    apiFetch('/api/auth/me/')
      .then((res) => {
        if (!res.ok) throw new Error(`me failed: ${res.status}`);
        return res.json();
      })
      .then((data) => {
        setConnError(false);
        if (data.authenticated) {
          setUsername(data.name || data.username);
          setIsAdmin(!!data.is_admin);
          setIsEngineer(data.role === 'engineer' || data.role === 'admin');
        } else {
          router.push('/login');
        }
      })
      .catch(() => setConnError(true));
  }, [router]);

  const handleLogout = async () => {
    try {
      await apiFetch('/api/auth/logout/', { method: 'POST' });
    } finally {
      router.push('/login');
    }
  };

  return (
    <Group h="100%" px="md" justify="space-between">
      <Title order={3} c="blue">Case-Flow UV</Title>
      <Group gap="xs">
        <Button
          component={Link}
          href="/"
          size="sm"
          variant={pathname === '/' ? 'light' : 'subtle'}
          leftSection={<IconList size={16} />}
        >
          케이스 목록
        </Button>
        <Button
          component={Link}
          href="/dashboard"
          size="sm"
          variant={pathname === '/dashboard' ? 'light' : 'subtle'}
          leftSection={<IconLayoutDashboard size={16} />}
        >
          대시보드
        </Button>
        <Button
          component={Link}
          href="/knowledge"
          size="sm"
          variant={pathname.startsWith('/knowledge') ? 'light' : 'subtle'}
          leftSection={<IconBook2 size={16} />}
        >
          지식 베이스
        </Button>
        {isAdmin && (
          <Button
            component={Link}
            href="/users"
            size="sm"
            variant={pathname === '/users' ? 'light' : 'subtle'}
            leftSection={<IconUsers size={16} />}
          >
            계정 관리
          </Button>
        )}
        {/* 리스트 페이지(/)는 status 필터 줄에 인라인 버튼이 있어 플로팅 생략.
            엔지니어 이상 사용 가능 (서버도 동일하게 차단) */}
        {pathname !== '/' && isEngineer && <HelpAgentWidget />}
        {connError && (
          <Group gap={6} ml="md">
            <Text size="sm" c="red" fw={600}>
              백엔드 서버에 연결할 수 없습니다 (:8000 확인)
            </Text>
            <Button
              size="sm"
              variant="subtle"
              color="gray"
              leftSection={<IconLogout size={16} />}
              onClick={() => router.push('/login')}
            >
              로그인 페이지로
            </Button>
          </Group>
        )}
        {username && (
          <Group gap={6} ml="md">
            <IconUserCircle size={18} color="var(--mantine-color-gray-6)" />
            <Text size="sm" c="dimmed">{username}</Text>
            <Button
              size="sm"
              variant="subtle"
              color="gray"
              leftSection={<IconLogout size={16} />}
              onClick={handleLogout}
            >
              로그아웃
            </Button>
          </Group>
        )}
      </Group>
    </Group>
  );
}
