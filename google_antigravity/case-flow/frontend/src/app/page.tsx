'use client';

import { useState, useEffect } from 'react';
import { useRouter } from 'next/navigation';
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
import { IconSearch, IconPlus, IconRefresh, IconMail, IconSparkles } from '@tabler/icons-react';
import NewCaseModal from './components/NewCaseModal';
import AppHeader from './components/AppHeader';
import HelpAgentWidget from './components/HelpAgentDrawer';
import ScrollToTopButton from './components/ScrollToTopButton';
import { apiFetch } from './lib/api';
import { useMe } from './lib/useMe';

interface Case {
  id: number;
  case_id: string;
  vendor: string;
  status: string;
  summary: string;
  description: string;
  device_model: string;
  device_serial: string;
  software_version: string;
  date: string;
}

interface ModelInfo {
  id: string;
  provider: string;
  note: string;
  key_configured: boolean;
}

const PROVIDER_LABELS: Record<string, string> = {
  anthropic: 'Anthropic Claude',
  openai: 'OpenAI',
  google: 'Google Gemini',
};

export default function Home() {
  const [activeTab, setActiveTab] = useState<string | null>('all');
  const [statusTab, setStatusTab] = useState<string | null>('all');
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState('15');
  const [searchQuery, setSearchQuery] = useState('');
  const [modalOpened, setModalOpened] = useState(false);
  const [cases, setCases] = useState<Case[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [syncing, setSyncing] = useState(false);
  const [syncMessage, setSyncMessage] = useState('');
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [currentModel, setCurrentModel] = useState<string | null>(null);
  const [modelSaving, setModelSaving] = useState(false);
  const router = useRouter();
  const { canWrite, isAdmin } = useMe();

  const fetchModelInfo = async () => {
    try {
      const response = await apiFetch('/api/settings/translation-model/');
      if (response.ok) {
        const data = await response.json();
        setModels(data.models);
        setCurrentModel(data.current);
      }
    } catch (error) {
      console.error('Error fetching model info:', error);
    }
  };

  const changeModel = async (model: string | null) => {
    if (!model || model === currentModel) return;
    setModelSaving(true);
    try {
      const response = await apiFetch('/api/settings/translation-model/', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model }),
      });
      const data = await response.json();
      if (response.ok) {
        setCurrentModel(data.current);
        setSyncMessage(`AI 분석 모델이 ${data.current}(으)로 변경되었습니다.`);
      } else {
        setSyncMessage(`모델 변경 실패: ${data.error || response.statusText}`);
      }
    } catch (error) {
      console.error('Error changing model:', error);
      setSyncMessage('모델 변경 실패: 백엔드 서버에 연결할 수 없습니다.');
    } finally {
      setModelSaving(false);
    }
  };

  const modelSelectData = Object.keys(PROVIDER_LABELS)
    .map((provider) => ({
      group: PROVIDER_LABELS[provider],
      items: models
        .filter((m) => m.provider === provider)
        .map((m) => ({
          value: m.id,
          label: `${m.id} (${m.note})`,
          disabled: !m.key_configured,
        })),
    }))
    .filter((group) => group.items.length > 0);

  const fetchCases = async () => {
    setLoading(true);
    setLoadError('');
    try {
      const response = await apiFetch('/api/cases/');
      if (response.ok) {
        const data = await response.json();
        setCases(data);
      } else {
        setLoadError(`케이스 목록을 불러오지 못했습니다 (HTTP ${response.status}).`);
      }
    } catch (error) {
      console.error('Error fetching cases:', error);
      setLoadError('백엔드 서버(:8000)에 연결할 수 없습니다. 서버 실행 상태를 확인하세요.');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchCases();
    fetchModelInfo();
  }, []);

  const syncGmail = async () => {
    setSyncing(true);
    setSyncMessage('');
    try {
      const response = await apiFetch('/api/gmail/sync/', { method: 'POST' });
      const data = await response.json();
      if (response.ok) {
        setSyncMessage(
          `동기화 완료: 메일 ${data.fetched}건 확인, 케이스 ${data.cases_created}건 생성, 메일 ${data.emails_added}건 등록` +
          (data.ignored > 0 ? `, 불필요 메일 ${data.ignored}건 제외` : '') +
          (data.no_vendor > 0 ? `, 벤더 미식별 ${data.no_vendor}건 보류` : '') +
          (data.errors > 0 ? `, 오류 ${data.errors}건` : '')
        );
        fetchCases();
      } else {
        setSyncMessage(`동기화 실패: ${data.error || response.statusText}`);
      }
    } catch (error) {
      console.error('Error syncing Gmail:', error);
      setSyncMessage('동기화 실패: 백엔드 서버에 연결할 수 없습니다.');
    } finally {
      setSyncing(false);
    }
  };

  const getVendorFilter = (tab: string | null) => {
    if (tab === 'all' || !tab) return null;
    if (tab === 'hpe') return 'HPE Aruba';
    if (tab === 'a10') return 'A10';
    if (tab === 'arista') return 'Arista';
    if (tab === 'juniper') return 'Juniper';
    return tab;
  };

  const filteredCases = cases.filter(c => {
    const vendorMatch = activeTab === 'all' || c.vendor === getVendorFilter(activeTab);
    const statusMatch = statusTab === 'all' || c.status === statusTab;
    const q = searchQuery.toLowerCase();
    const searchMatch = [c.summary, c.case_id, c.device_model, c.device_serial, c.software_version]
      .some((field) => (field || '').toLowerCase().includes(q));
    return vendorMatch && statusMatch && searchMatch;
  });

  // 상태 탭 건수는 현재 벤더 탭 기준으로 집계 (상태 필터 자신은 제외)
  const vendorFiltered = cases.filter(
    c => activeTab === 'all' || c.vendor === getVendorFilter(activeTab)
  );
  const statusCount = (s: string) =>
    s === 'all' ? vendorFiltered.length : vendorFiltered.filter(c => c.status === s).length;

  // 필터/페이지 크기가 바뀌면 1페이지로 복귀
  useEffect(() => {
    setPage(1);
  }, [activeTab, statusTab, searchQuery, pageSize]);

  const size = Number(pageSize);
  const totalPages = Math.max(1, Math.ceil(filteredCases.length / size));
  const pagedCases = filteredCases.slice((page - 1) * size, page * size);

  const rows = pagedCases.map((element) => (
    <Table.Tr 
      key={element.id} 
      onClick={() => router.push(`/cases/${element.id}`)}
      style={{ cursor: 'pointer' }}
    >
      <Table.Td style={{ whiteSpace: 'nowrap' }}><Text fw={500}>{element.case_id}</Text></Table.Td>
      <Table.Td style={{ whiteSpace: 'nowrap' }}>
        <Badge color={getVendorColor(element.vendor)} variant="light">
          {element.vendor}
        </Badge>
      </Table.Td>
      <Table.Td style={{ whiteSpace: 'nowrap' }}>
        <Badge color={getStatusColor(element.status)} variant="dot">
          {element.status}
        </Badge>
      </Table.Td>
      <Table.Td style={{ wordBreak: 'break-word' }}>{element.summary}</Table.Td>
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
        <Text size="sm">{element.date.split(' ')[0]}</Text>
        <Text size="sm" c="dimmed">{element.date.split(' ')[1]}</Text>
      </Table.Td>
    </Table.Tr>
  ));

  return (
    <AppShell
      header={{ height: 60 }}
      padding="md"
    >
      <AppShell.Header>
        <AppHeader />
      </AppShell.Header>

      <AppShell.Main>
        <Container size="xl">
          <Group justify="space-between" mb="lg">
            <div>
              <Title order={2}>Case Management</Title>
              <Text c="dimmed">Track and manage network vendor support cases</Text>
            </div>
            <Group>
                 {isAdmin && (
                 <Select
                    leftSection={<IconSparkles size={14} />}
                    placeholder="AI 분석 모델"
                    data={modelSelectData}
                    value={currentModel}
                    onChange={changeModel}
                    disabled={modelSaving}
                    w={300}
                    size="sm"
                    searchable={false}
                    allowDeselect={false}
                    comboboxProps={{ width: 340, position: 'bottom-end' }}
                 />
                 )}
                 {canWrite && (
                 <Button
                    leftSection={<IconMail size={14} />}
                    variant="light"
                    onClick={syncGmail}
                    loading={syncing}
                 >
                    Gmail 동기화
                 </Button>
                 )}
                 <Button leftSection={<IconRefresh size={14} />} variant="default" onClick={fetchCases}>
                    Refresh
                 </Button>
                {canWrite && (
                <Button leftSection={<IconPlus size={14} />} onClick={() => setModalOpened(true)}>
                    New Case
                </Button>
                )}
            </Group>
          </Group>

          {syncMessage && (
            <Text size="sm" c={syncMessage.startsWith('동기화 완료') ? 'teal' : 'red'} mb="sm">
              {syncMessage}
            </Text>
          )}

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
                    ? cases.length
                    : cases.filter((c) => c.vendor === getVendorFilter(t.value)).length;
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
              color={statusTab && statusTab !== 'all' ? getStatusColor(statusTab) : 'blue'}
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
                  { value: 'Open', label: 'Open' },
                  { value: 'Pending', label: 'Pending' },
                  { value: 'Resolved', label: 'Resolved' },
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
                            : `var(--mantine-color-${getStatusColor(s.value)}-6)`,
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
            {/* AI 비용 때문에 테스트 배포 동안 관리자에게만 노출 (서버도 차단) */}
            {isAdmin && <HelpAgentWidget variant="inline" />}
            </Group>

            <Group mb="md">
               <TextInput
                  placeholder="Search cases... (요약, 케이스 ID, 장비 모델, 시리얼, 버전)"
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
                    <Table.Th style={{ whiteSpace: 'nowrap', width: 90 }}>Case ID</Table.Th>
                    <Table.Th style={{ whiteSpace: 'nowrap', width: 110 }}>Vendor</Table.Th>
                    <Table.Th style={{ whiteSpace: 'nowrap', width: 110 }}>Status</Table.Th>
                    <Table.Th>Summary</Table.Th>
                    <Table.Th style={{ whiteSpace: 'nowrap', width: 150 }}>Device</Table.Th>
                    <Table.Th style={{ whiteSpace: 'nowrap', width: 110 }}>Date</Table.Th>
                    </Table.Tr>
                </Table.Thead>
                <Table.Tbody>{rows}</Table.Tbody>
                </Table>
                
                {rows.length === 0 && (
                <Text c="dimmed" ta="center" py="xl">No cases found</Text>
                )}

                {filteredCases.length > 0 && (
                <Paper bg="gray.0" p="sm" mt="md" radius="md">
                  <Group justify="space-between">
                    <Group gap={6}>
                      <Badge variant="light" color="blue" size="lg" radius="sm">
                        {filteredCases.length}건
                      </Badge>
                      <Text size="sm" c="dimmed">
                        중{' '}
                        <Text component="span" fw={600} c="dark">
                          {(page - 1) * size + 1}–{Math.min(page * size, filteredCases.length)}
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

        {/* 페이지 크기를 늘리면 목록이 길어지므로 맨 위로 복귀 버튼 */}
        <ScrollToTopButton />
      </AppShell.Main>

      <NewCaseModal
        opened={modalOpened} 
        onClose={() => setModalOpened(false)}
        onCaseCreated={fetchCases}
      />
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

function getStatusColor(status: string) {
  switch (status) {
    case 'Open': return 'blue';
    case 'Resolved': return 'green';
    case 'Pending': return 'yellow';
    default: return 'gray';
  }
}
