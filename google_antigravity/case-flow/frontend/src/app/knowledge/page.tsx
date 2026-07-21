'use client';

import { Suspense, useEffect, useRef, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import {
  AppShell,
  Group,
  Title,
  Text,
  Container,
  Tabs,
  Table,
  Badge,
  Button,
  Paper,
  TextInput,
  Loader,
  Center,
  Select,
  Pagination
} from '@mantine/core';
import {
  IconSearch,
  IconRefresh,
  IconChevronUp,
  IconChevronDown,
  IconSelector,
} from '@tabler/icons-react';
import AppHeader from '../components/AppHeader';
import ScrollToTopButton from '../components/ScrollToTopButton';
import { apiFetch } from '../lib/api';

interface KnowledgeItem {
  id: number;
  knowledge_id: string;
  vendor: string;
  title: string;
  problem: string;
  root_cause: string;
  resolution: string;
  device_model: string;
  software_version: string;
  status: string; // draft | confirmed
  analyzed_by: string;
  source_case: { id: number; case_id: string; status: string; vendor_case_number: string | null } | null;
  source_session: { id: number; title: string } | null;
  created_at: string;
  updated_at: string;
}

type SortKey = 'knowledge_id' | 'vendor' | 'status' | 'device' | 'date';

function sortValue(k: KnowledgeItem, key: SortKey): string | number {
  switch (key) {
    case 'knowledge_id': return k.id;
    case 'vendor': return k.vendor;
    case 'status': return k.status; // confirmed < draft (확정 먼저)
    case 'device': return k.device_model || '';
    case 'date': return k.created_at;
  }
}

function SortableTh({
  label, width, sorted, asc, onSort,
}: {
  label: string;
  width?: number;
  sorted: boolean;
  asc: boolean;
  onSort: () => void;
}) {
  const Icon = sorted ? (asc ? IconChevronUp : IconChevronDown) : IconSelector;
  return (
    <Table.Th
      style={{ whiteSpace: 'nowrap', width, cursor: 'pointer', userSelect: 'none' }}
      onClick={onSort}
      aria-sort={sorted ? (asc ? 'ascending' : 'descending') : 'none'}
    >
      <Group gap={4} wrap="nowrap">
        {label}
        <Icon
          size={14}
          color={sorted ? 'var(--mantine-color-blue-6)' : 'var(--mantine-color-gray-5)'}
        />
      </Group>
    </Table.Th>
  );
}

const SORT_KEYS: SortKey[] = ['knowledge_id', 'vendor', 'status', 'device', 'date'];

export default function KnowledgePage() {
  return (
    <Suspense>
      <KnowledgeListPage />
    </Suspense>
  );
}

function KnowledgeListPage() {
  // 케이스 목록과 동일하게 목록 상태를 URL 쿼리로 유지 — back으로 돌아와도 복원
  const searchParams = useSearchParams();
  const initialSort = searchParams.get('sort');
  const [activeTab, setActiveTab] = useState<string | null>(searchParams.get('vendor') || 'all');
  const [statusTab, setStatusTab] = useState<string | null>(searchParams.get('status') || 'all');
  const [page, setPage] = useState(Math.max(1, Number(searchParams.get('page')) || 1));
  const [pageSize, setPageSize] = useState(searchParams.get('size') || '15');
  const [searchQuery, setSearchQuery] = useState(searchParams.get('q') || '');
  const [items, setItems] = useState<KnowledgeItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [sortKey, setSortKey] = useState<SortKey | null>(
    SORT_KEYS.includes(initialSort as SortKey) ? (initialSort as SortKey) : null
  );
  const [sortAsc, setSortAsc] = useState(searchParams.get('dir') !== 'desc');
  const router = useRouter();

  const fetchItems = async () => {
    setLoading(true);
    setLoadError('');
    try {
      const response = await apiFetch('/api/knowledge/');
      if (response.ok) {
        setItems(await response.json());
      } else {
        setLoadError(`지식 목록을 불러오지 못했습니다 (HTTP ${response.status}).`);
      }
    } catch (error) {
      console.error('Error fetching knowledge:', error);
      setLoadError('백엔드 서버(:8000)에 연결할 수 없습니다. 서버 실행 상태를 확인하세요.');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchItems();
  }, []);

  const getVendorFilter = (tab: string | null) => {
    if (tab === 'all' || !tab) return null;
    if (tab === 'hpe') return 'HPE Aruba';
    if (tab === 'a10') return 'A10';
    if (tab === 'arista') return 'Arista';
    if (tab === 'juniper') return 'Juniper';
    return tab;
  };

  const filteredItems = items.filter(k => {
    const vendorMatch = activeTab === 'all' || k.vendor === getVendorFilter(activeTab);
    const statusMatch = statusTab === 'all' || k.status === statusTab;
    const q = searchQuery.toLowerCase();
    // 커맨드·에러 문자열 검색이 핵심 용도라 해결 본문까지 검색 대상에 포함
    const searchMatch = [k.title, k.problem, k.root_cause, k.resolution,
      k.knowledge_id, k.device_model, k.software_version, k.source_case?.case_id]
      .some((field) => (field || '').toLowerCase().includes(q));
    return vendorMatch && statusMatch && searchMatch;
  });

  const vendorFiltered = items.filter(
    k => activeTab === 'all' || k.vendor === getVendorFilter(activeTab)
  );
  const statusCount = (s: string) =>
    s === 'all' ? vendorFiltered.length : vendorFiltered.filter(k => k.status === s).length;

  const toggleSort = (key: SortKey) => {
    if (sortKey === key) {
      setSortAsc((v) => !v);
    } else {
      setSortKey(key);
      setSortAsc(true);
    }
  };

  const sortedItems = sortKey
    ? [...filteredItems].sort((a, b) => {
        const va = sortValue(a, sortKey);
        const vb = sortValue(b, sortKey);
        const cmp = typeof va === 'number'
          ? va - (vb as number)
          : String(va).localeCompare(String(vb));
        return sortAsc ? cmp : -cmp;
      })
    : filteredItems;

  const isFirstRender = useRef(true);
  useEffect(() => {
    if (isFirstRender.current) {
      isFirstRender.current = false;
      return;
    }
    setPage(1);
  }, [activeTab, statusTab, searchQuery, pageSize, sortKey, sortAsc]);

  useEffect(() => {
    const params = new URLSearchParams();
    if (activeTab && activeTab !== 'all') params.set('vendor', activeTab);
    if (statusTab && statusTab !== 'all') params.set('status', statusTab);
    if (searchQuery) params.set('q', searchQuery);
    if (pageSize !== '15') params.set('size', pageSize);
    if (sortKey) {
      params.set('sort', sortKey);
      if (!sortAsc) params.set('dir', 'desc');
    }
    if (page > 1) params.set('page', String(page));
    const qs = params.toString();
    window.history.replaceState(null, '', qs ? `?${qs}` : window.location.pathname);
  }, [activeTab, statusTab, searchQuery, pageSize, sortKey, sortAsc, page]);

  // 검색어 사용 측정 (케이스 목록과 동일한 디바운스 비콘)
  useEffect(() => {
    const q = searchQuery.trim();
    if (!q) return;
    const timer = setTimeout(() => {
      apiFetch('/api/usage/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ event: 'search', detail: `[knowledge] ${q}` }),
      }).catch(() => {});
    }, 1500);
    return () => clearTimeout(timer);
  }, [searchQuery]);

  const size = Number(pageSize);
  const totalPages = Math.max(1, Math.ceil(sortedItems.length / size));
  const pagedItems = sortedItems.slice((page - 1) * size, page * size);

  useEffect(() => {
    if (!loading && page > totalPages) setPage(totalPages);
  }, [loading, page, totalPages]);

  const rows = pagedItems.map((element) => (
    <Table.Tr
      key={element.id}
      onClick={() => router.push(`/knowledge/${element.id}`)}
      style={{ cursor: 'pointer' }}
    >
      <Table.Td style={{ whiteSpace: 'nowrap' }}><Text fw={500}>{element.knowledge_id}</Text></Table.Td>
      <Table.Td style={{ whiteSpace: 'nowrap' }}>
        <Badge color={getVendorColor(element.vendor)} variant="light">
          {element.vendor}
        </Badge>
      </Table.Td>
      <Table.Td style={{ whiteSpace: 'nowrap' }}>
        <Badge color={getKnowledgeStatusColor(element.status)} variant="dot">
          {element.status === 'confirmed' ? '확정' : 'AI 초안'}
        </Badge>
      </Table.Td>
      <Table.Td style={{ wordBreak: 'break-word' }}>{element.title}</Table.Td>
      <Table.Td style={{ whiteSpace: 'nowrap' }}>
        {element.device_model ? (
          <>
            <Text size="sm" fw={500}>{element.device_model}</Text>
            {element.software_version && (
              <Text size="xs" c="dimmed">v{element.software_version}</Text>
            )}
          </>
        ) : (
          <Text size="sm" c="dimmed">—</Text>
        )}
      </Table.Td>
      <Table.Td style={{ whiteSpace: 'nowrap' }}>
        {element.source_case ? (
          <Text size="sm" fw={500}>{element.source_case.case_id}</Text>
        ) : element.source_session ? (
          <Text size="sm" fw={500} c="grape">AI 대화</Text>
        ) : (
          <Text size="sm" c="dimmed">—</Text>
        )}
      </Table.Td>
      <Table.Td style={{ whiteSpace: 'nowrap' }}>
        <Text size="sm">{element.created_at.slice(0, 10)}</Text>
      </Table.Td>
    </Table.Tr>
  ));

  return (
    <AppShell header={{ height: 60 }} padding="md">
      <AppShell.Header>
        <AppHeader />
      </AppShell.Header>

      <AppShell.Main>
        <Container size="xl">
          <Group justify="space-between" mb="lg">
            <div>
              <Title order={2}>Knowledge Base</Title>
              <Text c="dimmed">해결된 케이스에서 추출한 문제-원인-해결 지식</Text>
            </div>
            <Button leftSection={<IconRefresh size={14} />} variant="default" onClick={fetchItems}>
              Refresh
            </Button>
          </Group>

          <Paper shadow="xs" p="md" withBorder>
            <Tabs
              value={activeTab}
              onChange={setActiveTab}
              mb="md"
              color={activeTab && activeTab !== 'all'
                ? getVendorColor(getVendorFilter(activeTab) ?? '')
                : 'blue'}
              styles={{
                tab: {
                  fontSize: 'var(--mantine-font-size-md)',
                  fontWeight: 600,
                  paddingTop: 12,
                  paddingBottom: 12,
                  borderBottomWidth: 4,
                },
              }}
            >
              <Tabs.List>
                {[
                  { value: 'all', label: 'All Vendors', color: 'blue' },
                  { value: 'a10', label: 'A10', color: 'orange' },
                  { value: 'arista', label: 'Arista', color: 'blue' },
                  { value: 'hpe', label: 'HPE Aruba', color: 'green' },
                  { value: 'juniper', label: 'Juniper', color: 'violet' },
                ].map((t) => {
                  const active = activeTab === t.value;
                  const count = t.value === 'all'
                    ? items.length
                    : items.filter((k) => k.vendor === getVendorFilter(t.value)).length;
                  return (
                    <Tabs.Tab
                      key={t.value}
                      value={t.value}
                      style={active ? { color: `var(--mantine-color-${t.color}-7)` } : undefined}
                      leftSection={t.value !== 'all' && (
                        <span style={{
                          width: 10, height: 10, borderRadius: 3, display: 'inline-block',
                          background: `var(--mantine-color-${t.color}-6)`,
                          opacity: active ? 1 : 0.4,
                        }} />
                      )}
                      rightSection={
                        <Badge
                          size="sm"
                          variant={active ? 'filled' : 'light'}
                          color={active ? t.color : 'gray'}
                          radius="xl"
                        >
                          {count}
                        </Badge>
                      }
                    >
                      {t.label}
                    </Tabs.Tab>
                  );
                })}
              </Tabs.List>
            </Tabs>

            <Group justify="space-between" align="center" mb="md">
              <Tabs
                value={statusTab}
                onChange={setStatusTab}
                variant="pills"
                radius="xl"
                autoContrast
                color={statusTab && statusTab !== 'all' ? getKnowledgeStatusColor(statusTab) : 'blue'}
              >
                <Tabs.List
                  style={{
                    display: 'inline-flex',
                    gap: 4,
                    padding: 4,
                    borderRadius: 999,
                    background: 'var(--mantine-color-gray-0)',
                    border: '1px solid var(--mantine-color-gray-2)',
                  }}
                >
                  {[
                    { value: 'all', label: 'All Status' },
                    { value: 'draft', label: 'AI 초안' },
                    { value: 'confirmed', label: '확정' },
                  ].map((s) => {
                    const active = statusTab === s.value;
                    return (
                      <Tabs.Tab
                        key={s.value}
                        value={s.value}
                        style={{ fontWeight: active ? 700 : 500 }}
                        leftSection={s.value !== 'all' && (
                          <span style={{
                            width: 8, height: 8, borderRadius: 4, display: 'inline-block',
                            background: active
                              ? 'currentColor'
                              : `var(--mantine-color-${getKnowledgeStatusColor(s.value)}-6)`,
                            opacity: active ? 0.9 : 0.6,
                          }} />
                        )}
                      >
                        {s.label} ({statusCount(s.value)})
                      </Tabs.Tab>
                    );
                  })}
                </Tabs.List>
              </Tabs>
            </Group>

            <Group mb="md">
              <TextInput
                placeholder="Search knowledge... (증상, 에러 메시지, 커맨드, 장비 모델, K/C-번호)"
                leftSection={<IconSearch size={14} />}
                style={{ flex: 1 }}
                value={searchQuery}
                onChange={(event) => setSearchQuery(event.currentTarget.value)}
              />
            </Group>

            {loading ? (
              <Center py="xl">
                <Loader size="lg" />
              </Center>
            ) : loadError ? (
              <Center py="xl">
                <Text c="red" fw={600}>{loadError}</Text>
              </Center>
            ) : (
              <>
                <Table highlightOnHover verticalSpacing="sm">
                  <Table.Thead>
                    <Table.Tr>
                      <SortableTh label="ID" width={80} sorted={sortKey === 'knowledge_id'} asc={sortAsc} onSort={() => toggleSort('knowledge_id')} />
                      <SortableTh label="Vendor" width={110} sorted={sortKey === 'vendor'} asc={sortAsc} onSort={() => toggleSort('vendor')} />
                      <SortableTh label="Status" width={110} sorted={sortKey === 'status'} asc={sortAsc} onSort={() => toggleSort('status')} />
                      <Table.Th>문제 요약</Table.Th>
                      <SortableTh label="Device" width={150} sorted={sortKey === 'device'} asc={sortAsc} onSort={() => toggleSort('device')} />
                      <Table.Th style={{ whiteSpace: 'nowrap', width: 90 }}>출처</Table.Th>
                      <SortableTh label="Date" width={110} sorted={sortKey === 'date'} asc={sortAsc} onSort={() => toggleSort('date')} />
                    </Table.Tr>
                  </Table.Thead>
                  <Table.Tbody>{rows}</Table.Tbody>
                </Table>

                {rows.length === 0 && (
                  <Text c="dimmed" ta="center" py="xl">
                    {items.length === 0
                      ? '아직 지식 항목이 없습니다. 서버에서 extract_knowledge 커맨드로 해결된 케이스에서 추출할 수 있습니다.'
                      : 'No knowledge found'}
                  </Text>
                )}

                {filteredItems.length > 0 && (
                  <Paper bg="gray.0" p="sm" mt="md" radius="md">
                    <Group justify="space-between">
                      <Group gap={6}>
                        <Badge variant="light" color="blue" size="lg" radius="sm">
                          {filteredItems.length}건
                        </Badge>
                        <Text size="sm" c="dimmed">
                          중{' '}
                          <Text component="span" fw={600} c="dark">
                            {(page - 1) * size + 1}–{Math.min(page * size, filteredItems.length)}
                          </Text>
                          {' '}표시
                        </Text>
                      </Group>
                      <Pagination
                        value={page}
                        onChange={setPage}
                        total={totalPages}
                        radius="xl"
                        withEdges
                        siblings={1}
                        boundaries={1}
                        styles={{
                          control: { border: 'none', fontWeight: 600 },
                        }}
                      />
                      <Select
                        value={pageSize}
                        onChange={(v) => v && setPageSize(v)}
                        data={[
                          { value: '15', label: '15개씩' },
                          { value: '30', label: '30개씩' },
                          { value: '50', label: '50개씩' },
                        ]}
                        w={110}
                        size="xs"
                        radius="xl"
                        allowDeselect={false}
                      />
                    </Group>
                  </Paper>
                )}
              </>
            )}
          </Paper>
        </Container>

        <ScrollToTopButton />
      </AppShell.Main>
    </AppShell>
  );
}

function getVendorColor(vendor: string) {
  switch (vendor) {
    case 'A10': return 'orange';
    case 'Arista': return 'blue';
    case 'HPE Aruba': return 'green';
    case 'Juniper': return 'violet';
    default: return 'gray';
  }
}

function getKnowledgeStatusColor(status: string) {
  switch (status) {
    case 'confirmed': return 'green';
    case 'draft': return 'yellow';
    default: return 'gray';
  }
}
