'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { Group, Title, Button } from '@mantine/core';
import { IconLayoutDashboard, IconList } from '@tabler/icons-react';

export default function AppHeader() {
  const pathname = usePathname();

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
      </Group>
    </Group>
  );
}
