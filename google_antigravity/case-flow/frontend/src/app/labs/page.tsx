'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import {
  ActionIcon, Alert, AppShell, Badge, Button, Card, Group, Loader, Menu, Paper,
  ScrollArea, Select, Stack, Text, Textarea, Title, Tooltip,
} from '@mantine/core';
import {
  IconAlertTriangle, IconCheck, IconCircleCheck, IconPlayerPlay, IconPlayerStop,
  IconRefresh, IconSend, IconServerOff, IconTerminal2, IconX,
} from '@tabler/icons-react';
import AppHeader from '../components/AppHeader';
import { apiFetch } from '../lib/api';
import TopologyCanvas from './TopologyCanvas';
import {
  LABS, MOCK_CHAT, MOCK_STEPS,
  type ChatMessage, type LabNode, type NodeState, type RunStep,
} from './mockData';

// 설계 검토용 목업 화면 — 백엔드 연동 전이라 상태 전이를 프론트에서 흉내낸다.
// 확인하려는 것 세 가지:
//  ① 랩을 갈아끼울 수 있는가 (랩이 계속 늘어난다는 전제)
//  ② "부팅 완료"가 꺼짐/기동 중/준비됨 3단계로 읽히는가
//  ③ 왼쪽 조작과 오른쪽 대화·결과가 한 화면에서 같이 보이는가

const READY_LABEL: Record<NodeState, string> = {
  off: '꺼짐', booting: '기동 중', ready: '준비됨',
};

// 노드가 EVE-NG에서 뜬 뒤 관리 API가 응답하기까지 걸리는 시간(목업용 근사).
// 실제로는 백엔드가 벤더별로 찔러보고 SSE로 알려준다 — A10 aXAPI 인증,
// Arista eAPI show version, 리눅스 호스트는 ping/SSH.
const BOOT_MS: Record<string, number> = { a10: 9000, veos: 5000, arubacx: 5000, iol: 3000, linux: 3000 };

export default function LabsPage() {
  const [labFile, setLabFile] = useState(LABS[0].file);
  const [nodes, setNodes] = useState<LabNode[]>(LABS[0].nodes);
  const [selected, setSelected] = useState<LabNode | null>(null);
  const [steps] = useState<RunStep[]>(MOCK_STEPS);
  const [chat, setChat] = useState<ChatMessage[]>(MOCK_CHAT);
  const [input, setInput] = useState('');
  const timers = useRef<ReturnType<typeof setTimeout>[]>([]);
  // EVE-NG 설정 여부. 랩 서버가 없어도 이 화면은 열려야 하므로, 빈 화면 대신
  // 무엇을 해야 하는지 알려준다. 자격증명은 내려받지 않고 주소만 표시한다.
  const [eveng, setEveng] = useState<{ configured: boolean; server: string } | null>(null);

  useEffect(() => {
    apiFetch('/api/labs/config/')
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => data && setEveng(data))
      .catch(() => {});
  }, []);

  const lab = useMemo(() => LABS.find((l) => l.file === labFile)!, [labFile]);

  // 랩이 바뀌면 노드 상태를 새 랩 것으로 갈아끼운다. effect가 아니라 렌더 중에
  // 조정하는 이유: effect에서 setState하면 한 프레임 동안 이전 랩의 노드가 새 랩
  // 이름으로 그려진다(그리고 cascading render 경고도 난다).
  const [loadedFile, setLoadedFile] = useState(labFile);
  if (loadedFile !== labFile) {
    setLoadedFile(labFile);
    setNodes(lab.nodes);
    setSelected(null);
  }

  // 기동 타이머 정리는 부수효과 — 랩을 바꾸거나 화면을 떠날 때 정리하지 않으면
  // 이전 랩의 타이머가 새 랩의 노드 목록을 덮어쓴다.
  useEffect(() => () => {
    timers.current.forEach(clearTimeout);
    timers.current = [];
  }, [labFile]);

  const setNodeState = (id: number, state: NodeState) =>
    setNodes((prev) => prev.map((n) => (n.id === id ? { ...n, state } : n)));

  // 켜기 = 즉시 '기동 중', 관리 API가 응답할 때쯤 '준비됨'. 이 2단계가 이 화면의
  // 핵심이다 — EVE-NG의 status만 보면 프로세스가 떴는지까지만 알 수 있다.
  const powerOn = (node: LabNode, delay = 0) => {
    if (node.state !== 'off') return;
    const t1 = setTimeout(() => {
      setNodeState(node.id, 'booting');
      const t2 = setTimeout(
        () => setNodeState(node.id, 'ready'),
        BOOT_MS[node.template] ?? 5000,
      );
      timers.current.push(t2);
    }, delay);
    timers.current.push(t1);
  };

  const powerOff = (node: LabNode) => setNodeState(node.id, 'off');

  // 전체 켜기는 순차 기동 — 9노드를 한꺼번에 띄우면 공용 EVE-NG가 휘청이고
  // 부팅도 서로 느려진다. 무거운 것(A10)부터 간격을 두고 올린다.
  const startAll = () => {
    [...nodes]
      .sort((a, b) => b.ram - a.ram)
      .forEach((n, i) => powerOn(n, i * 1500));
  };

  const stopAll = () => {
    timers.current.forEach(clearTimeout);
    timers.current = [];
    setNodes((prev) => prev.map((n) => ({ ...n, state: 'off' })));
  };

  const ready = nodes.filter((n) => n.state === 'ready').length;
  const booting = nodes.filter((n) => n.state === 'booting').length;
  const allReady = nodes.length > 0 && ready === nodes.length;
  const totalRam = nodes.reduce((s, n) => s + n.ram, 0);
  const totalCpu = nodes.reduce((s, n) => s + n.cpu, 0);

  const send = () => {
    const text = input.trim();
    if (!text) return;
    setChat((prev) => [...prev, { role: 'user', text }, {
      role: 'assistant',
      text: allReady
        ? '(목업) 랩이 모두 준비됐습니다. 실제 연동 시 여기에서 상태를 조회하고 테스트를 제안합니다.'
        : `(목업) 아직 ${nodes.length - ready}대가 준비되지 않아 테스트를 시작할 수 없습니다.`,
    }]);
    setInput('');
  };

  const labSelectData = useMemo(() => {
    const groups = new Map<string, { value: string; label: string }[]>();
    LABS.forEach((l) => {
      const items = groups.get(l.vendor) ?? [];
      items.push({ value: l.file, label: l.name });
      groups.set(l.vendor, items);
    });
    return [...groups.entries()].map(([group, items]) => ({ group, items }));
  }, []);

  return (
    <AppShell header={{ height: 60 }} padding="md">
      <AppShell.Header><AppHeader /></AppShell.Header>

      <AppShell.Main>
        <Group justify="space-between" mb="sm" wrap="nowrap">
          <Group gap="sm" wrap="nowrap">
            <Title order={2}>Lab Tests</Title>
            {/* 랩은 계속 늘어난다 — 목록은 EVE-NG에서 읽어오고, 벤더로 묶고,
                검색 가능하게 둔다. 지금 8개지만 20개가 넘어도 쓸 수 있어야 한다. */}
            <Select
              w={280}
              searchable
              allowDeselect={false}
              value={labFile}
              onChange={(v) => v && setLabFile(v)}
              data={labSelectData}
              comboboxProps={{ width: 320 }}
            />
            <Text size="sm" c="dimmed">{lab.description}</Text>
          </Group>
          <Group gap="sm" wrap="nowrap">
            {eveng?.configured && (
              <Text size="xs" c="dimmed" ff="monospace">{eveng.server}</Text>
            )}
            <Button variant="default" size="sm" leftSection={<IconRefresh size={16} />}>
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
            아래 화면은 실제 랩이 아니라 예시 데이터입니다. 서버를 연결하려면{' '}
            <code>.env</code>에 <code>CASEFLOW_EVENG_URL</code> ·{' '}
            <code>CASEFLOW_EVENG_USER</code> · <code>CASEFLOW_EVENG_PASSWORD</code>를
            넣고 백엔드를 다시 시작하세요. (운영 VM은 <code>caseflow-up.sh</code>로 기동해야
            <code>.env.age</code>의 값이 들어갑니다.)
          </Alert>
        )}

        <Group align="stretch" gap="md" wrap="nowrap" style={{ height: 'calc(100vh - 140px)' }}>
          {/* ─────────────── 왼쪽: 랩 조작 + 토폴로지 ─────────────── */}
          <Paper withBorder radius="md" p="md" style={{ flex: '1 1 62%', display: 'flex', flexDirection: 'column' }}>
            <Group justify="space-between" mb="sm" wrap="nowrap">
              <Group gap="xs">
                <Button
                  size="sm"
                  leftSection={<IconPlayerPlay size={16} />}
                  disabled={nodes.length === 0 || nodes.every((n) => n.state !== 'off')}
                  onClick={startAll}
                >
                  전체 켜기
                </Button>
                <Button
                  size="sm" variant="default"
                  leftSection={<IconPlayerStop size={16} />}
                  disabled={nodes.every((n) => n.state === 'off')}
                  onClick={stopAll}
                >
                  전체 끄기
                </Button>
                {nodes.length > 0 && (
                  // 공용 EVE-NG라 이 랩이 얼마를 먹는지 누르기 전에 보여준다
                  <Tooltip label="이 랩 전체를 켰을 때 EVE-NG에서 점유하는 자원">
                    <Badge variant="light" color="gray" size="lg">
                      {(totalRam / 1024).toFixed(0)}GB · {totalCpu} vCPU
                    </Badge>
                  </Tooltip>
                )}
              </Group>

              {/* 말씀하신 "다 켜졌는지 확인시켜주는 알람" — 부팅 완료 기준이다 */}
              <Group gap="xs" wrap="nowrap">
                {nodes.length === 0 ? null : allReady ? (
                  <Badge size="lg" color="teal" leftSection={<IconCircleCheck size={14} />}>
                    전체 준비됨 {ready}/{nodes.length}
                  </Badge>
                ) : (
                  <Badge
                    size="lg"
                    color={booting > 0 ? 'yellow' : 'gray'}
                    leftSection={booting > 0 ? <Loader size={11} color="yellow" /> : <IconAlertTriangle size={14} />}
                  >
                    준비 {ready}/{nodes.length}
                    {booting > 0 && ` · 기동 중 ${booting}`}
                  </Badge>
                )}
              </Group>
            </Group>

            <div style={{ flex: 1, minHeight: 0 }}>
              <TopologyCanvas lab={{ ...lab, nodes }} selectedId={selected?.id ?? null} onSelect={setSelected} />
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
                    }} />
                    <Text size="xs" c="dimmed">{READY_LABEL[s]}</Text>
                  </Group>
                ))}
              </Group>

              {selected ? (
                <Group gap="xs" wrap="nowrap">
                  <Text size="sm" fw={600}>{selected.name}</Text>
                  <Badge size="sm" variant="light"
                         color={selected.state === 'ready' ? 'teal' : selected.state === 'booting' ? 'yellow' : 'gray'}>
                    {READY_LABEL[selected.state]}
                  </Badge>
                  <Text size="xs" c="dimmed">{selected.ram / 1024}GB · {selected.cpu} vCPU</Text>
                  <Menu position="top-end">
                    <Menu.Target>
                      <Button size="compact-sm" variant="light">동작</Button>
                    </Menu.Target>
                    <Menu.Dropdown>
                      <Menu.Item
                        leftSection={<IconPlayerPlay size={14} />}
                        disabled={selected.state !== 'off'}
                        onClick={() => powerOn(selected)}
                      >
                        켜기
                      </Menu.Item>
                      <Menu.Item
                        leftSection={<IconPlayerStop size={14} />}
                        disabled={selected.state === 'off'}
                        onClick={() => powerOff(selected)}
                      >
                        끄기
                      </Menu.Item>
                      <Menu.Item leftSection={<IconTerminal2 size={14} />}>
                        콘솔 열기 ({selected.console.split('://')[0]})
                      </Menu.Item>
                    </Menu.Dropdown>
                  </Menu>
                </Group>
              ) : (
                <Text size="xs" c="dimmed">노드를 클릭하면 개별 켜기·끄기·콘솔을 열 수 있습니다</Text>
              )}
            </Group>
          </Paper>

          {/* ─────────────── 오른쪽: 진행 상황 + AI 대화 ─────────────── */}
          <Stack gap="md" style={{ flex: '1 1 38%', minWidth: 380 }}>
            <Paper withBorder radius="md" p="md">
              <Text fw={700} size="sm" mb="sm">진행 상황 · 테스트 결과</Text>
              <Stack gap="xs">
                {steps.map((s, i) => (
                  <Group key={i} gap="sm" align="flex-start" wrap="nowrap">
                    <div style={{ width: 18, paddingTop: 2 }}>
                      {s.state === 'done' && <IconCheck size={16} color="var(--mantine-color-teal-6)" />}
                      {s.state === 'failed' && <IconX size={16} color="var(--mantine-color-red-6)" />}
                      {s.state === 'running' && <Loader size={14} />}
                      {s.state === 'pending' && (
                        <div style={{
                          width: 10, height: 10, borderRadius: 5, marginLeft: 3, marginTop: 3,
                          border: '2px solid var(--mantine-color-gray-4)',
                        }} />
                      )}
                    </div>
                    <div style={{ flex: 1 }}>
                      <Group gap="xs">
                        <Text size="sm" fw={s.state === 'running' ? 600 : 400}
                              c={s.state === 'pending' ? 'dimmed' : undefined}>
                          {s.label}
                        </Text>
                        {s.elapsed && <Text size="xs" c="dimmed">{s.elapsed}</Text>}
                      </Group>
                      {s.detail && <Text size="xs" c="dimmed">{s.detail}</Text>}
                    </div>
                  </Group>
                ))}
              </Stack>
            </Paper>

            <Paper withBorder radius="md" p="md"
                   style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
              <Text fw={700} size="sm" mb="sm">AI 대화</Text>
              <ScrollArea style={{ flex: 1 }} offsetScrollbars>
                <Stack gap="sm">
                  {chat.map((m, i) => (
                    <div key={i}>
                      <Paper
                        p="sm" radius="md"
                        bg={m.role === 'user' ? 'blue.0' : 'gray.0'}
                        ml={m.role === 'user' ? 'xl' : 0}
                        mr={m.role === 'user' ? 0 : 'xl'}
                      >
                        <Text size="sm" style={{ whiteSpace: 'pre-wrap', lineHeight: 1.65 }}>{m.text}</Text>
                      </Paper>
                      {/* 설정을 바꾸는 제안은 사람이 승인해야 나간다. 프롬프트가 아니라
                          코드로 막는 자리 — 여기 버튼을 누르기 전에는 실행되지 않는다. */}
                      {m.proposal && (
                        <Card withBorder radius="md" p="sm" mt={6} mr="xl">
                          <Group justify="space-between" mb={6} wrap="nowrap">
                            <Text size="xs" fw={700} c="orange">승인 필요 · {m.proposal.title}</Text>
                          </Group>
                          <Paper bg="dark.8" p="xs" radius="sm" mb="xs">
                            <Text size="xs" c="gray.3"
                                  style={{ fontFamily: 'var(--mantine-font-family-monospace)', whiteSpace: 'pre-wrap' }}>
                              {m.proposal.commands.join('\n')}
                            </Text>
                          </Paper>
                          <Group gap="xs">
                            <Button size="compact-sm" color="orange" leftSection={<IconCheck size={14} />}>
                              승인하고 적용
                            </Button>
                            <Button size="compact-sm" variant="default">거절</Button>
                          </Group>
                        </Card>
                      )}
                    </div>
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
      </AppShell.Main>
    </AppShell>
  );
}
