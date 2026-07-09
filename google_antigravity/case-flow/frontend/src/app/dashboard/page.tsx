'use client';

import { useState, useEffect } from 'react';
import {
  AppShell,
  Container,
  Title,
  Text,
  Paper,
  Group,
  Badge,
  Stack,
  Loader,
  Center,
  SimpleGrid,
  SegmentedControl,
  Tooltip,
  Divider,
} from '@mantine/core';
import { IconArrowUpRight, IconRefreshDot } from '@tabler/icons-react';
import AppHeader from '../components/AppHeader';
import { apiUrl } from '../lib/api';

interface VendorStats {
  vendor: string;
  total: number;
  open: number;
  pending: number;
  resolved: number;
  recent_created: number;
  recent_updated: number;
}

interface DashboardStats {
  days: number;
  vendors: VendorStats[];
  totals: Omit<VendorStats, 'vendor'>;
}

// 벤더 고유색 — 목록 화면의 배지 색과 같은 계열 (CVD/대비 검증 완료 팔레트)
const VENDOR_COLORS: Record<string, string> = {
  'A10': '#e8590c',
  'Arista': '#228be6',
  'HPE Aruba': '#2f9e44',
  'Juniper': '#7950f2',
};

const STATUS_META = [
  { key: 'open' as const, label: 'Open', color: 'blue' },
  { key: 'pending' as const, label: 'Pending', color: 'yellow' },
  { key: 'resolved' as const, label: 'Resolved', color: 'green' },
];

export default function DashboardPage() {
  const [days, setDays] = useState('7');
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    fetch(apiUrl(`/api/dashboard/stats/?days=${days}`))
      .then((res) => {
        if (res.ok) return res.json();
        throw new Error('Failed to fetch stats');
      })
      .then(setStats)
      .catch((err) => console.error(err))
      .finally(() => setLoading(false));
  }, [days]);

  return (
    <AppShell header={{ height: 60 }} padding="md">
      <AppShell.Header>
        <AppHeader />
      </AppShell.Header>

      <AppShell.Main>
        <Container size="xl">
          <Group justify="space-between" mb="lg">
            <div>
              <Title order={2}>대시보드</Title>
              <Text c="dimmed">벤더별 케이스 현황 요약</Text>
            </div>
            <SegmentedControl
              value={days}
              onChange={setDays}
              data={[
                { label: '최근 7일', value: '7' },
                { label: '최근 14일', value: '14' },
                { label: '최근 30일', value: '30' },
              ]}
            />
          </Group>

          {loading || !stats ? (
            <Center py="xl"><Loader size="lg" /></Center>
          ) : (
            <Stack gap="lg">
              {/* 전체 상태 요약 */}
              <SimpleGrid cols={{ base: 2, sm: 4 }}>
                <StatTile label="전체 케이스" value={stats.totals.total} />
                {STATUS_META.map((s) => (
                  <StatTile
                    key={s.key}
                    label={s.label}
                    value={stats.totals[s.key]}
                    badgeColor={s.color}
                  />
                ))}
              </SimpleGrid>

              {/* 벤더별 분포 그래프 */}
              <Paper shadow="xs" p="xl" withBorder>
                <Title order={4} mb="xs">벤더별 케이스 분포</Title>
                <Text size="sm" c="dimmed" mb="md">전체 {stats.totals.total}건</Text>
                <VendorBarChart vendors={stats.vendors} />
              </Paper>

              {/* 벤더별 상태 + 최근 활동 카드 */}
              <SimpleGrid cols={{ base: 1, sm: 2, lg: 4 }}>
                {stats.vendors.map((v) => (
                  <VendorCard key={v.vendor} stats={v} days={stats.days} />
                ))}
              </SimpleGrid>
            </Stack>
          )}
        </Container>
      </AppShell.Main>
    </AppShell>
  );
}

function StatTile({ label, value, badgeColor }: {
  label: string;
  value: number;
  badgeColor?: string;
}) {
  return (
    <Paper shadow="xs" p="md" withBorder>
      <Group gap="xs">
        {badgeColor && <Badge color={badgeColor} variant="dot" size="sm">{label}</Badge>}
        {!badgeColor && <Text size="sm" c="dimmed">{label}</Text>}
      </Group>
      <Text fz={32} fw={700} mt={4}>{value}</Text>
    </Paper>
  );
}

function VendorBarChart({ vendors }: { vendors: VendorStats[] }) {
  const max = Math.max(...vendors.map((v) => v.total), 1);

  return (
    <Stack gap={10}>
      {vendors.map((v) => (
        <Tooltip
          key={v.vendor}
          label={`${v.vendor} — 총 ${v.total}건 · Open ${v.open} · Pending ${v.pending} · Resolved ${v.resolved}`}
          position="top-start"
          withArrow
        >
          <Group gap="sm" wrap="nowrap">
            <Text size="sm" w={90} style={{ flexShrink: 0 }}>{v.vendor}</Text>
            <div style={{ flex: 1, background: 'var(--mantine-color-gray-1)', borderRadius: 4, height: 20 }}>
              <div
                style={{
                  width: `${(v.total / max) * 100}%`,
                  height: '100%',
                  background: VENDOR_COLORS[v.vendor] ?? 'var(--mantine-color-gray-5)',
                  borderRadius: '0 4px 4px 0',
                  minWidth: v.total > 0 ? 4 : 0,
                }}
              />
            </div>
            <Text size="sm" fw={600} w={40} ta="right" style={{ flexShrink: 0 }}>{v.total}</Text>
          </Group>
        </Tooltip>
      ))}
    </Stack>
  );
}

function VendorCard({ stats, days }: { stats: VendorStats; days: number }) {
  return (
    <Paper shadow="xs" p="md" withBorder>
      <Group justify="space-between" mb="sm">
        <Badge color={getVendorBadgeColor(stats.vendor)} variant="light" size="lg">
          {stats.vendor}
        </Badge>
        <Text fw={700} fz="xl">{stats.total}</Text>
      </Group>

      <Stack gap={6}>
        {STATUS_META.map((s) => (
          <Group key={s.key} justify="space-between">
            <Badge color={s.color} variant="dot" size="sm">{s.label}</Badge>
            <Text size="sm" fw={600}>{stats[s.key]}</Text>
          </Group>
        ))}
      </Stack>

      <Divider my="sm" />

      <Text size="xs" c="dimmed" mb={4}>최근 {days}일</Text>
      <Group gap="lg">
        <Group gap={4}>
          <IconArrowUpRight size={14} color="var(--mantine-color-teal-6)" />
          <Text size="sm">신규 <Text component="span" fw={700}>{stats.recent_created}</Text></Text>
        </Group>
        <Group gap={4}>
          <IconRefreshDot size={14} color="var(--mantine-color-blue-6)" />
          <Text size="sm">업데이트 <Text component="span" fw={700}>{stats.recent_updated}</Text></Text>
        </Group>
      </Group>
    </Paper>
  );
}

function getVendorBadgeColor(vendor: string) {
  switch (vendor) {
    case 'A10': return 'orange';
    case 'Arista': return 'blue';
    case 'HPE Aruba': return 'green';
    case 'Juniper': return 'violet';
    default: return 'gray';
  }
}
