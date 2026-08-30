'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  ActionIcon, Alert, AppShell, Badge, Button, Group, Loader, Menu, Modal, Paper,
  ScrollArea, Select, Stack, Table, Text, Textarea, TextInput, Title, Tooltip,
} from '@mantine/core';
import {
  IconAlertTriangle, IconCheck, IconCirclePlus, IconPlayerPlay, IconPlayerStop,
  IconRefresh, IconSend, IconServerOff, IconTerminal2, IconTrash,
} from '@tabler/icons-react';
import AppHeader from '../components/AppHeader';
import { apiFetch } from '../lib/api';
import { useMe } from '../lib/useMe';
import TopologyCanvas from './TopologyCanvas';
import { nodeState, type AvailableLab, type LabDetail, type LabNode, type LabSummary, type NodeState } from './types';

// Step 1 — EVE-NG를 실제로 읽는다. 전원 제어와 준비 판정은 Step 2에서 붙는다.
// 지금 화면이 알 수 있는 상태는 꺼짐 / 기동 중(프로세스가 떴음)까지다.

const READY_LABEL: Record<NodeState, string> = {
  off: '꺼짐', booting: '기동 중', ready: '준비됨',
};

const STEPS = [
  { label: '토폴로지 확인', hint: 'EVE-NG 배선과 대조' },
  { label: '사전 상태 수집', hint: '장비 관리 API 응답' },
  { label: '설정 적용', hint: '블루프린트 실행' },
  { label: '검증', hint: '코드 판정' },
  { label: '롤백', hint: '적용 원장 역순' },
];

export default function LabsPage() {
  const { isAdmin } = useMe();
  const [eveng, setEveng] = useState<{ configured: boolean; server: string } | null>(null);
  const [labs, setLabs] = useState<LabSummary[]>([]);
  const [labId, setLabId] = useState<string | null>(null);
  const [lab, setLab] = useState<LabDetail | null>(null);
  const [selected, setSelected] = useState<LabNode | null>(null);
  const [loading, setLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState('');

  const [registerOpen, setRegisterOpen] = useState(false);
  const [available, setAvailable] = useState<AvailableLab[] | null>(null);
  const [form, setForm] = useState({ path: '', name: '', vendor: '', description: '' });
  const [registering, setRegistering] = useState(false);

  const [chat, setChat] = useState<{ role: 'user' | 'assistant'; text: string }[]>([]);
  const [input, setInput] = useState('');

  useEffect(() => {
    apiFetch('/api/labs/config/')
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => data && setEveng(data))
      .catch(() => {});
  }, []);

  const loadLabs = useCallback(async () => {
    try {
      const res = await apiFetch('/api/labs/');
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data: LabSummary[] = await res.json();
      setLabs(data);
      setLabId((prev) => prev ?? (data.length > 0 ? String(data[0].id) : null));
    } catch {
      setError('랩 목록을 불러오지 못했습니다.');
    }
  }, []);

  useEffect(() => { loadLabs(); }, [loadLabs]);

  useEffect(() => {
    if (!labId) return;
    let cancelled = false;
    setLoading(true);
    setSelected(null);
    apiFetch(`/api/labs/${labId}/`)
      .then((res) => (res.ok ? res.json() : Promise.reject(new Error(`HTTP ${res.status}`))))
      .then((data: LabDetail) => { if (!cancelled) { setLab(data); setError(''); } })
      .catch(() => { if (!cancelled) setError('토폴로지를 불러오지 못했습니다.'); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [labId]);

  const refresh = async () => {
    if (!labId) return;
    setRefreshing(true);
    setError('');
    try {
      const res = await apiFetch(`/api/labs/${labId}/refresh/`, { method: 'POST' });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
      setLab(data);
      setSelected(null);
      loadLabs();
    } catch (e) {
      setError(`토폴로지 갱신 실패: ${e instanceof Error ? e.message : e}`);
    } finally {
      setRefreshing(false);
    }
  };

  const openRegister = async () => {
    setRegisterOpen(true);
    setAvailable(null);
    try {
      const res = await apiFetch('/api/labs/available/');
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
      setAvailable(data.labs);
    } catch (e) {
      setError(`EVE-NG 랩 목록을 불러오지 못했습니다: ${e instanceof Error ? e.message : e}`);
      setRegisterOpen(false);
    }
  };

  const register = async () => {
    setRegistering(true);
    try {
      const res = await apiFetch('/api/labs/register/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(form),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
      setRegisterOpen(false);
      setForm({ path: '', name: '', vendor: '', description: '' });
      await loadLabs();
      setLabId(String(data.id));
    } catch (e) {
      setError(`등록 실패: ${e instanceof Error ? e.message : e}`);
    } finally {
      setRegistering(false);
    }
  };

  const unregister = async () => {
    if (!lab) return;
    if (!window.confirm(`${lab.name} 등록을 해제할까요? EVE-NG의 랩은 지워지지 않습니다.`)) return;
    const res = await apiFetch('/api/labs/register/', {
      method: 'DELETE',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: lab.id }),
    });
    if (res.ok) {
      setLab(null);
      setLabId(null);
      await loadLabs();
    } else {
      setError('등록 해제에 실패했습니다.');
    }
  };

  const nodes = lab?.nodes ?? [];
  const running = nodes.filter((n) => n.running).length;
  const totalRam = nodes.reduce((s, n) => s + n.ram, 0);
  const totalCpu = nodes.reduce((s, n) => s + n.cpu, 0);
  const nodeLinkCount = (lab?.links ?? []).filter(
    (l) => !l.source_is_network && !l.target_is_network).length;

  const labSelectData = useMemo(() => {
    const groups = new Map<string, { value: string; label: string }[]>();
    labs.forEach((l) => {
      const key = l.vendor || '미분류';
      const items = groups.get(key) ?? [];
      items.push({ value: String(l.id), label: l.name });
      groups.set(key, items);
    });
    return [...groups.entries()].map(([group, items]) => ({ group, items }));
  }, [labs]);

  const send = () => {
    const text = input.trim();
    if (!text) return;
    setChat((prev) => [...prev, { role: 'user', text }, {
      role: 'assistant',
      text: '랩 에이전트는 아직 연결되지 않았습니다 (Step 5). '
        + '지금은 왼쪽에서 토폴로지를 확인할 수 있습니다.',
    }]);
    setInput('');
  };

  return (
    <AppShell header={{ height: 60 }} padding="md">
      <AppShell.Header><AppHeader /></AppShell.Header>

      <AppShell.Main>
        <Group justify="space-between" mb="sm" wrap="nowrap">
          <Group gap="sm" wrap="nowrap">
            <Title order={2}>Lab Tests</Title>
            {/* 랩은 계속 늘어난다 — 벤더로 묶고 검색 가능하게 둔다 */}
            <Select
              w={280}
              searchable
              allowDeselect={false}
              placeholder={labs.length ? '랩 선택' : '등록된 랩이 없습니다'}
              disabled={labs.length === 0}
              value={labId}
              onChange={setLabId}
              data={labSelectData}
              comboboxProps={{ width: 320 }}
            />
            {lab && <Text size="sm" c="dimmed">{lab.description}</Text>}
          </Group>
          <Group gap="sm" wrap="nowrap">
            {eveng?.configured && (
              <Text size="xs" c="dimmed" ff="monospace">{eveng.server}</Text>
            )}
            {isAdmin && (
              <Button variant="light" size="sm" leftSection={<IconCirclePlus size={16} />}
                      onClick={openRegister}>
                랩 등록
              </Button>
            )}
            <Button
              variant="default" size="sm" leftSection={<IconRefresh size={16} />}
              loading={refreshing} disabled={!labId} onClick={refresh}
            >
              토폴로지 갱신
            </Button>
          </Group>
        </Group>

        {eveng && !eveng.configured && (
          <Alert
            color="orange" variant="light" mb="sm"
            icon={<IconServerOff size={18} />}
            title="EVE-NG 랩 서버가 설정되지 않았습니다"
          >
            <code>.env</code>에 <code>CASEFLOW_EVENG_URL</code> ·{' '}
            <code>CASEFLOW_EVENG_USER</code> · <code>CASEFLOW_EVENG_PASSWORD</code>를
            넣고 백엔드를 다시 시작하세요. (운영 VM은 <code>caseflow-up.sh</code>로 기동해야
            <code>.env.age</code>의 값이 들어갑니다.)
          </Alert>
        )}

        {error && (
          <Alert color="red" variant="light" mb="sm" withCloseButton
                 onClose={() => setError('')} icon={<IconAlertTriangle size={18} />}>
            {error}
          </Alert>
        )}

        <Group align="stretch" gap="md" wrap="nowrap" style={{ height: 'calc(100vh - 140px)' }}>
          {/* ─────────────── 왼쪽: 랩 조작 + 토폴로지 ─────────────── */}
          <Paper withBorder radius="md" p="md"
                 style={{ flex: '1 1 62%', display: 'flex', flexDirection: 'column' }}>
            <Group justify="space-between" mb="sm" wrap="nowrap">
              <Group gap="xs">
                {/* 전원 제어는 Step 2에서 연결한다. 지금 누르면 아무 일도 없어야
                    하므로 비활성으로 두고 이유를 툴팁에 적는다. */}
                <Tooltip label="전원 제어는 다음 단계에서 연결됩니다">
                  <Button size="sm" leftSection={<IconPlayerPlay size={16} />} disabled>
                    전체 켜기
                  </Button>
                </Tooltip>
                <Tooltip label="전원 제어는 다음 단계에서 연결됩니다">
                  <Button size="sm" variant="default"
                          leftSection={<IconPlayerStop size={16} />} disabled>
                    전체 끄기
                  </Button>
                </Tooltip>
                {nodes.length > 0 && (
                  <Tooltip label="이 랩 전체를 켰을 때 EVE-NG에서 점유하는 자원">
                    <Badge variant="light" color="gray" size="lg">
                      {(totalRam / 1024).toFixed(0)}GB · {totalCpu} vCPU
                    </Badge>
                  </Tooltip>
                )}
              </Group>

              {nodes.length > 0 && (
                <Badge size="lg" color={running > 0 ? 'yellow' : 'gray'}>
                  기동 중 {running}/{nodes.length}
                </Badge>
              )}
            </Group>

            <div style={{ flex: 1, minHeight: 0, position: 'relative' }}>
              {loading ? (
                <Group justify="center" h="100%"><Loader /></Group>
              ) : (
                <TopologyCanvas
                  lab={lab}
                  selectedName={selected?.name ?? null}
                  onSelect={setSelected}
                />
              )}
            </div>

            <Group justify="space-between" mt="sm" wrap="nowrap">
              <Group gap="lg">
                {(['ready', 'booting', 'off'] as NodeState[]).map((s) => (
                  <Group key={s} gap={6}>
                    <div style={{
                      width: 10, height: 10, borderRadius: 3,
                      background: s === 'ready' ? 'var(--mantine-color-teal-5)'
                        : s === 'booting' ? 'var(--mantine-color-yellow-5)'
                        : 'var(--mantine-color-gray-4)',
                      opacity: s === 'ready' ? 0.4 : 1,
                    }} />
                    <Text size="xs" c="dimmed">
                      {READY_LABEL[s]}{s === 'ready' && ' (다음 단계)'}
                    </Text>
                  </Group>
                ))}
              </Group>

              {selected ? (
                <Group gap="xs" wrap="nowrap">
                  <Text size="sm" fw={600}>{selected.name}</Text>
                  <Badge size="sm" variant="light"
                         color={selected.running ? 'yellow' : 'gray'}>
                    {READY_LABEL[nodeState(selected)]}
                  </Badge>
                  <Text size="xs" c="dimmed">
                    {selected.image} · {selected.ram / 1024}GB · {selected.cpu} vCPU
                  </Text>
                  <Menu position="top-end">
                    <Menu.Target>
                      <Button size="compact-sm" variant="light">동작</Button>
                    </Menu.Target>
                    <Menu.Dropdown>
                      <Menu.Item leftSection={<IconPlayerPlay size={14} />} disabled>
                        켜기 (다음 단계)
                      </Menu.Item>
                      <Menu.Item leftSection={<IconPlayerStop size={14} />} disabled>
                        끄기 (다음 단계)
                      </Menu.Item>
                      <Menu.Item
                        leftSection={<IconTerminal2 size={14} />}
                        component="a" href={selected.console_url}
                      >
                        콘솔 열기 ({selected.console_url.split('://')[0]})
                      </Menu.Item>
                    </Menu.Dropdown>
                  </Menu>
                </Group>
              ) : lab ? (
                <Text size="xs" c="dimmed">
                  노드 {nodes.length} · 링크 {nodeLinkCount} ·{' '}
                  {lab.topology_synced_at
                    ? `수집 ${new Date(lab.topology_synced_at).toLocaleString('ko-KR')}`
                    : '미수집'}
                </Text>
              ) : null}
            </Group>
          </Paper>

          {/* ─────────────── 오른쪽: 진행 상황 + AI 대화 ─────────────── */}
          <Stack gap="md" style={{ flex: '1 1 38%', minWidth: 380 }}>
            <Paper withBorder radius="md" p="md">
              <Text fw={700} size="sm" mb="sm">진행 상황 · 테스트 결과</Text>
              <Stack gap="xs">
                {STEPS.map((s) => (
                  <Group key={s.label} gap="sm" align="flex-start" wrap="nowrap">
                    <div style={{
                      width: 10, height: 10, borderRadius: 5, marginLeft: 3, marginTop: 7,
                      border: '2px solid var(--mantine-color-gray-4)',
                    }} />
                    <div>
                      <Text size="sm" c="dimmed">{s.label}</Text>
                      <Text size="xs" c="dimmed">{s.hint}</Text>
                    </div>
                  </Group>
                ))}
              </Stack>
              <Text size="xs" c="dimmed" mt="sm">
                실행 엔진은 Step 4에서 연결됩니다.
              </Text>
            </Paper>

            <Paper withBorder radius="md" p="md"
                   style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
              <Text fw={700} size="sm" mb="sm">AI 대화</Text>
              <ScrollArea style={{ flex: 1 }} offsetScrollbars>
                <Stack gap="sm">
                  {chat.length === 0 && (
                    <Text size="sm" c="dimmed">
                      랩 에이전트는 Step 5에서 연결됩니다. 지금은 왼쪽에서 EVE-NG의
                      실제 토폴로지를 확인할 수 있습니다.
                    </Text>
                  )}
                  {chat.map((m, i) => (
                    <Paper
                      key={i} p="sm" radius="md"
                      bg={m.role === 'user' ? 'blue.0' : 'gray.0'}
                      ml={m.role === 'user' ? 'xl' : 0}
                      mr={m.role === 'user' ? 0 : 'xl'}
                    >
                      <Text size="sm" style={{ whiteSpace: 'pre-wrap', lineHeight: 1.65 }}>
                        {m.text}
                      </Text>
                    </Paper>
                  ))}
                </Stack>
              </ScrollArea>
              <Group gap="xs" mt="sm" align="flex-end" wrap="nowrap">
                <Textarea
                  style={{ flex: 1 }}
                  autosize minRows={1} maxRows={4}
                  placeholder="랩 상태를 묻거나 테스트를 요청하세요"
                  value={input}
                  onChange={(e) => setInput(e.currentTarget.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
                      e.preventDefault();
                      send();
                    }
                  }}
                />
                <ActionIcon size="lg" onClick={send} aria-label="보내기">
                  <IconSend size={18} />
                </ActionIcon>
              </Group>
            </Paper>
          </Stack>
        </Group>

        {/* 등록은 EVE-NG의 랩을 이 메뉴에 올리는 것일 뿐 EVE-NG를 바꾸지 않는다.
            EVE-NG에는 다른 사람 작업용 랩이 섞여 있어 전부 노출하지 않는다. */}
        <Modal opened={registerOpen} onClose={() => setRegisterOpen(false)}
               title="랩 등록" size="lg">
          {available === null ? (
            <Group justify="center" py="xl"><Loader /></Group>
          ) : (
            <Stack gap="md">
              <Text size="sm" c="dimmed">
                EVE-NG에 있는 랩 중 Case-Flow 메뉴에 올릴 것을 고릅니다.
                EVE-NG의 랩은 변경되지 않습니다.
              </Text>
              <ScrollArea h={220}>
                <Table highlightOnHover>
                  <Table.Tbody>
                    {available.map((l) => (
                      <Table.Tr key={l.path}
                                style={{ cursor: l.registered ? 'default' : 'pointer' }}
                                onClick={() => !l.registered && setForm({
                                  ...form, path: l.path,
                                  name: l.file.replace(/\.unl$/, ''),
                                })}>
                        <Table.Td>
                          <Text size="sm" ff="monospace"
                                c={l.registered ? 'dimmed' : undefined}>
                            {l.path}
                          </Text>
                        </Table.Td>
                        <Table.Td w={90}>
                          {l.registered
                            ? <Badge size="sm" color="gray" variant="light">등록됨</Badge>
                            : form.path === l.path
                              ? <Badge size="sm" color="blue">선택</Badge>
                              : null}
                        </Table.Td>
                      </Table.Tr>
                    ))}
                  </Table.Tbody>
                </Table>
              </ScrollArea>
              <TextInput label="표시 이름" value={form.name}
                         onChange={(e) => setForm({ ...form, name: e.currentTarget.value })} />
              <Group grow>
                <TextInput label="벤더" description="셀렉터 그룹으로 쓰입니다"
                           value={form.vendor}
                           onChange={(e) => setForm({ ...form, vendor: e.currentTarget.value })} />
                <TextInput label="설명" value={form.description}
                           onChange={(e) => setForm({ ...form, description: e.currentTarget.value })} />
              </Group>
              <Group justify="space-between">
                {lab && (
                  <Button variant="subtle" color="red" leftSection={<IconTrash size={16} />}
                          onClick={() => { setRegisterOpen(false); unregister(); }}>
                    현재 랩 등록 해제
                  </Button>
                )}
                <Group gap="xs" ml="auto">
                  <Button variant="default" onClick={() => setRegisterOpen(false)}>취소</Button>
                  <Button leftSection={<IconCheck size={16} />} loading={registering}
                          disabled={!form.path || !form.name} onClick={register}>
                    등록
                  </Button>
                </Group>
              </Group>
            </Stack>
          )}
        </Modal>
      </AppShell.Main>
    </AppShell>
  );
}
