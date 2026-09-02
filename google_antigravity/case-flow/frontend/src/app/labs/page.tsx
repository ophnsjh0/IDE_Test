'use client';

import { Suspense, useCallback, useEffect, useMemo, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import {
  ActionIcon, Alert, AppShell, Badge, Button, Card, Center, Divider, Group, Loader,
  Menu, Modal, Paper,
  ScrollArea, Select, Stack, Table, Text, Textarea, TextInput, Title, Tooltip,
} from '@mantine/core';
import {
  IconAlertTriangle, IconCheck, IconCircleCheck, IconCirclePlus,
  IconPlayerPlay, IconPlayerStop,
  IconKey, IconListCheck, IconRefresh, IconSend, IconServerOff,
  IconArrowBackUp, IconPlayerTrackNext, IconTerminal2, IconTrash, IconX,
  IconExternalLink, IconBulb, IconBook,
} from '@tabler/icons-react';
import AppHeader from '../components/AppHeader';
import { apiFetch } from '../lib/api';
import { useMe } from '../lib/useMe';
import TopologyCanvas from './TopologyCanvas';
import {
  DRIVERS, fallbackState,
  type AvailableLab, type LabDetail, type LabNode, type LabStatus,
  type Blueprint, type CheckReport, type LabSummary, type NodeAccess,
  type NodeState, type Proposal, type Recipe, type Run,
  type AccessPayload, type IpWarning,
} from './types';

// Step 1 — EVE-NG를 실제로 읽는다. 전원 제어와 준비 판정은 Step 2에서 붙는다.
// 지금 화면이 알 수 있는 상태는 꺼짐 / 기동 중(프로세스가 떴음)까지다.

const READY_LABEL: Record<NodeState, string> = {
  off: '꺼짐', booting: '기동 중', ready: '준비됨', unknown: '확인 불가',
};
const STATE_COLOR: Record<NodeState, string> = {
  off: 'gray', booting: 'yellow', ready: 'teal', unknown: 'violet',
};

// 상태 폴링 주기. 부팅은 분 단위로 진행되고 프로브가 노드마다 최대 3초 걸리므로
// 이보다 촘촘하게 볼 이유가 없다.
const POLL_MS = 5000;

const STEPS = [
  { label: '토폴로지 확인', hint: 'EVE-NG 배선과 대조' },
  { label: '사전 상태 수집', hint: '장비 관리 API 응답' },
  { label: '설정 적용', hint: '블루프린트 실행' },
  { label: '검증', hint: '코드 판정' },
  { label: '롤백', hint: '적용 원장 역순' },
];

function LabsPageInner() {
  const { isAdmin } = useMe();
  const router = useRouter();
  const searchParams = useSearchParams();
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

  const [status, setStatus] = useState<LabStatus | null>(null);
  const [powering, setPowering] = useState(false);
  const [accessOpen, setAccessOpen] = useState(false);
  const [access, setAccess] = useState<NodeAccess[]>([]);
  const [savingAccess, setSavingAccess] = useState(false);
  // IP는 서버 전체 기준으로 겹친다 — 랩이 pnet0을 공유하기 때문이다.
  // 경고는 IP를 적는 그 자리에서 보여야 고칠 수 있다.
  const [ipWarnings, setIpWarnings] = useState<IpWarning[]>([]);
  const [freeIps, setFreeIps] = useState<string[]>([]);
  const [dataSubnet, setDataSubnet] = useState('');
  const [suggestedSubnet, setSuggestedSubnet] = useState('');

  const [check, setCheck] = useState<CheckReport | null>(null);
  const [checking, setChecking] = useState(false);
  const [blueprints, setBlueprints] = useState<Blueprint[]>([]);
  const [run, setRun] = useState<Run | null>(null);
  const [running, setRunning] = useState(false);
  // 이 실행이 무엇을 재현하려는 것인지. 비워두면 그냥 랩 테스트다 —
  // 채우면 결과가 케이스 이력과 지식으로 돌아간다.
  const [runCase, setRunCase] = useState<string | null>(null);
  const [cases, setCases] = useState<{ value: string; label: string }[] | null>(null);
  const [saving, setSaving] = useState(false);
  // 검증된 명령 사전 — 랩에 매이지 않으므로 랩을 바꿔도 다시 받지 않는다
  const [recipesOpen, setRecipesOpen] = useState(false);
  const [recipes, setRecipes] = useState<Recipe[] | null>(null);
  const [recipeQuery, setRecipeQuery] = useState('');
  const [saveResult, setSaveResult] = useState<{ ok: boolean; text: string } | null>(null);

  type ChatMsg = { role: 'user' | 'assistant'; text: string; proposals?: Proposal[] };
  const [chat, setChat] = useState<ChatMsg[]>([]);
  const [input, setInput] = useState('');
  const [thinking, setThinking] = useState(false);

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
    setCheck(null);   // 이전 랩의 결과가 남아 있으면 오해한다
    // 다른 랩의 실행 결과는 지운다. 같은 랩이면 남긴다 — 지식·케이스에서
    // 실행 하나를 딥링크로 열면 labId가 뒤늦게 잡히는데, 무조건 지우면
    // 열자마자 사라진다.
    setRun((prev) => (prev && String(prev.lab.id) === labId ? prev : null));
    apiFetch(`/api/labs/${labId}/blueprints/`)
      .then((r) => (r.ok ? r.json() : []))
      .then(setBlueprints)
      .catch(() => setBlueprints([]));
    apiFetch(`/api/labs/${labId}/`)
      .then((res) => (res.ok ? res.json() : Promise.reject(new Error(`HTTP ${res.status}`))))
      .then((data: LabDetail) => { if (!cancelled) { setLab(data); setError(''); } })
      .catch(() => { if (!cancelled) setError('토폴로지를 불러오지 못했습니다.'); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [labId]);

  // 상태는 주기적으로 다시 묻는다. SSE 대신 폴링인 이유: 부팅이 분 단위로
  // 진행되고 백엔드도 결국 EVE-NG를 폴링해야 해서, 긴 연결을 유지하는 값이
  // 크지 않다. 랩을 바꾸거나 화면을 떠나면 타이머가 정리된다.
  useEffect(() => {
    if (!labId) { setStatus(null); return; }
    let cancelled = false;
    const tick = async () => {
      try {
        const res = await apiFetch(`/api/labs/${labId}/status/`);
        if (!res.ok) return;
        const data: LabStatus = await res.json();
        if (!cancelled) setStatus(data);
      } catch { /* 폴링 실패는 조용히 넘긴다 — 다음 주기에 다시 본다 */ }
    };
    tick();
    const timer = setInterval(tick, POLL_MS);
    return () => { cancelled = true; clearInterval(timer); };
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

  const power = async (action: 'start' | 'stop', nodeNames?: string[]) => {
    if (!labId) return;
    if (action === 'start' && !nodeNames) {
      // 공용 EVE-NG라 전체 켜기는 확인을 받는다 — 이 랩만 44GB를 먹는다
      const ok = window.confirm(
        `${nodes.length}대를 켭니다. EVE-NG에서 ${(totalRam / 1024).toFixed(0)}GB · `
        + `${totalCpu} vCPU를 점유합니다.\n\n계속할까요?`);
      if (!ok) return;
    }
    setPowering(true);
    setError('');
    try {
      const res = await apiFetch(`/api/labs/${labId}/power/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action, ...(nodeNames ? { nodes: nodeNames } : {}) }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
    } catch (e) {
      setError(`전원 조작 실패: ${e instanceof Error ? e.message : e}`);
    } finally {
      setPowering(false);
    }
  };

  const runCheck = async () => {
    if (!labId) return;
    setChecking(true);
    setError('');
    try {
      const res = await apiFetch(`/api/labs/${labId}/check/`, { method: 'POST' });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
      setCheck(data);
    } catch (e) {
      setError(`점검 실패: ${e instanceof Error ? e.message : e}`);
    } finally {
      setChecking(false);
    }
  };

  // 케이스 목록은 드롭다운을 열 때 한 번만 받는다 — 랩 화면에 들어올 때마다
  // 전체 케이스를 끌어오면 쓰지도 않을 목록에 매번 값을 치른다.
  const loadCases = useCallback(async () => {
    if (cases !== null) return;
    try {
      const res = await apiFetch('/api/cases/');
      const data = res.ok ? await res.json() : [];
      setCases(data.map((c: { id: number; case_id: string; summary: string }) => ({
        value: String(c.id), label: `${c.case_id} · ${c.summary}`,
      })));
    } catch {
      setCases([]);
    }
  }, [cases]);

  // 케이스 화면의 "랩에서 재현"으로 들어온 경우 재현 대상을 미리 잡아둔다
  useEffect(() => {
    const fromCase = searchParams.get('case');
    if (fromCase) { setRunCase(fromCase); loadCases(); }
  }, [searchParams, loadCases]);

  // 지식·케이스에서 "이 실행 보기"로 들어온 경우 그 실행을 연다
  useEffect(() => {
    const runId = searchParams.get('run');
    if (!runId) return;
    apiFetch(`/api/labs/runs/${runId}/`)
      .then((r) => (r.ok ? r.json() : null))
      .then((data: Run | null) => {
        if (!data) return;
        setLabId(String(data.lab.id));
        setRun(data);
      })
      .catch(() => {});
  }, [searchParams]);

  const startRun = async (blueprintId: number) => {
    setRunning(true);
    setError('');
    try {
      const res = await apiFetch(`/api/labs/${labId}/runs/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          blueprint: blueprintId,
          ...(runCase ? { case: Number(runCase) } : {}),
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
      setRun(data);
      setSaveResult(null);
    } catch (e) {
      setError(`실행 실패: ${e instanceof Error ? e.message : e}`);
    } finally {
      setRunning(false);
    }
  };

  // 사전은 (벤더, OS 버전)이 키라 랩과 무관하다 — 열 때 한 번만 받는다.
  const loadRecipes = useCallback(async (q = '') => {
    setRecipes(null);
    try {
      const res = await apiFetch(`/api/labs/recipes/?q=${encodeURIComponent(q)}`);
      setRecipes(res.ok ? await res.json() : []);
    } catch {
      setRecipes([]);
    }
  }, []);

  // 실행 결과를 지식으로 남긴다. 자동으로 만들지 않는 이유는 랩이 시행착오로도
  // 돌아가는 곳이라서 — 돌린 만큼 초안이 쌓이면 지식 베이스가 묽어진다.
  const saveKnowledge = async () => {
    if (!run) return;
    setSaving(true);
    setSaveResult(null);
    try {
      const res = await apiFetch(`/api/labs/runs/${run.id}/knowledge/`, { method: 'POST' });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
      setSaveResult({
        ok: true,
        text: data.outcome === 'exists'
          ? `이미 지식으로 등록된 실행입니다 (${data.item.knowledge_id}).`
          : `${data.item.knowledge_id}로 등록했습니다 — "${data.item.title}"`,
      });
    } catch (e) {
      setSaveResult({ ok: false, text: e instanceof Error ? e.message : String(e) });
    } finally {
      setSaving(false);
    }
  };

  // 롤백은 실행과 무관하게 사람이 누른다 — 실행이 죽었어도 원장만 있으면 되돌아간다
  const doRollback = async () => {
    if (!run) return;
    setRunning(true);
    try {
      const res = await apiFetch(`/api/labs/runs/${run.id}/rollback/`, { method: 'POST' });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
      setRun(data);
    } catch (e) {
      setError(`롤백 실패: ${e instanceof Error ? e.message : e}`);
    } finally {
      setRunning(false);
    }
  };

  const applyAccessPayload = (data: AccessPayload) => {
    const byName = new Map((data.rows ?? []).map((a) => [a.node_name, a]));
    // 토폴로지의 모든 노드를 한 줄씩 보여준다 — 어디가 비었는지 보이는 게 목적
    setAccess(nodes.map((n) => byName.get(n.name) ?? {
      node_name: n.name, role: '', mgmt_ip: '', driver: 'none',
      username: '', has_password: false,
    }));
    setIpWarnings(data.warnings ?? []);
    setFreeIps(data.free_ips ?? []);
    setDataSubnet(data.data_subnet ?? '');
    setSuggestedSubnet(data.suggested_data_subnet ?? '');
  };

  const openAccess = async () => {
    if (!labId) return;
    setAccessOpen(true);
    const res = await apiFetch(`/api/labs/${labId}/access/`);
    if (res.ok) applyAccessPayload(await res.json());
  };

  const saveAccess = async () => {
    if (!labId) return;
    setSavingAccess(true);
    try {
      const res = await apiFetch(`/api/labs/${labId}/access/`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ rows: access, data_subnet: dataSubnet }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      // 저장한 값 기준으로 경고를 다시 받는다. 닫지 않는 이유: 겹치는 IP를
      // 적었으면 그 자리에서 봐야 고친다 (저장은 막지 않는다).
      const data: AccessPayload = await res.json();
      applyAccessPayload(data);
      if ((data.warnings ?? []).length === 0) setAccessOpen(false);
    } catch (e) {
      setError(`접속 정보 저장 실패: ${e instanceof Error ? e.message : e}`);
    } finally {
      setSavingAccess(false);
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
  const stateOf = (n: LabNode): NodeState => status?.states[n.name] ?? fallbackState(n);
  const ready = status?.counts.ready ?? 0;
  const booting = status?.counts.booting ?? 0;
  const unknown = status?.counts.unknown ?? 0;
  const allOff = nodes.length > 0 && nodes.every((n) => stateOf(n) === 'off');
  const allReady = nodes.length > 0 && ready === nodes.length;
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

  const send = async () => {
    const text = input.trim();
    if (!text || !labId || thinking) return;
    const history: ChatMsg[] = [...chat, { role: 'user', text }];
    setChat(history);
    setInput('');
    setThinking(true);
    try {
      const res = await apiFetch(`/api/labs/${labId}/chat/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          messages: history.map((m) => ({ role: m.role, content: m.text })),
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
      setChat((prev) => [...prev, {
        role: 'assistant', text: data.reply, proposals: data.proposals,
      }]);
    } catch (e) {
      setChat((prev) => [...prev, {
        role: 'assistant',
        text: `답변을 받지 못했습니다: ${e instanceof Error ? e.message : e}`,
      }]);
    } finally {
      setThinking(false);
    }
  };

  // 제안 승인·거절 — 여기가 실행 게이트다. 에이전트는 제안까지만 만들 수 있고,
  // 실제 적용은 이 버튼을 눌렀을 때 서버가 한다.
  const decide = async (proposal: Proposal, decision: 'approve' | 'reject') => {
    setThinking(true);
    try {
      const res = await apiFetch(`/api/labs/proposals/${proposal.id}/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ decision }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
      setChat((prev) => prev.map((m) => ({
        ...m,
        proposals: m.proposals?.map((p) =>
          (p.id === proposal.id ? { ...p, status: data.status } : p)),
      })));
      if (data.run) setRun(data.run);
    } catch (e) {
      setError(`제안 처리 실패: ${e instanceof Error ? e.message : e}`);
    } finally {
      setThinking(false);
    }
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
              variant="light" size="sm" leftSection={<IconKey size={16} />}
              disabled={!lab || nodes.length === 0} onClick={openAccess}
            >
              접속 정보
            </Button>
            {/* 랩에 매이지 않는다 — 어느 랩을 보고 있든 같은 사전이다 */}
            <Button
              variant="light" size="sm" leftSection={<IconBook size={16} />}
              onClick={() => { setRecipesOpen(true); loadRecipes(recipeQuery); }}
            >
              명령 사전
            </Button>
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
                <Button
                  size="sm" leftSection={<IconPlayerPlay size={16} />}
                  loading={powering} disabled={nodes.length === 0 || !allOff}
                  onClick={() => power('start')}
                >
                  전체 켜기
                </Button>
                <Button
                  size="sm" variant="default" leftSection={<IconPlayerStop size={16} />}
                  loading={powering} disabled={nodes.length === 0 || allOff}
                  onClick={() => power('stop')}
                >
                  전체 끄기
                </Button>
                {nodes.length > 0 && (
                  <Tooltip label="이 랩 전체를 켰을 때 EVE-NG에서 점유하는 자원">
                    <Badge variant="light" color="gray" size="lg">
                      {(totalRam / 1024).toFixed(0)}GB · {totalCpu} vCPU
                    </Badge>
                  </Tooltip>
                )}
              </Group>

              {/* 말씀하신 "다 켜졌는지 확인시켜주는 알람" — 부팅 완료 기준이다 */}
              {nodes.length > 0 && (
                <Group gap="xs" wrap="nowrap">
                  {unknown > 0 && (
                    <Tooltip label={`접속 정보 미등록: ${status?.unprobeable.join(', ')}`}>
                      <Badge size="lg" color="violet" variant="light"
                             style={{ cursor: 'pointer' }} onClick={openAccess}>
                        확인 불가 {unknown}
                      </Badge>
                    </Tooltip>
                  )}
                  {allReady ? (
                    <Badge size="lg" color="teal"
                           leftSection={<IconCircleCheck size={14} />}>
                      전체 준비됨 {ready}/{nodes.length}
                    </Badge>
                  ) : (
                    <Badge
                      size="lg" color={booting > 0 ? 'yellow' : 'gray'}
                      leftSection={booting > 0
                        ? <Loader size={11} color="yellow" />
                        : <IconAlertTriangle size={14} />}
                    >
                      준비 {ready}/{nodes.length}
                      {booting > 0 && ` · 기동 중 ${booting}`}
                    </Badge>
                  )}
                </Group>
              )}
            </Group>

            <div style={{ flex: 1, minHeight: 0, position: 'relative' }}>
              {loading ? (
                <Group justify="center" h="100%"><Loader /></Group>
              ) : (
                <TopologyCanvas
                  lab={lab}
                  states={status?.states ?? {}}
                  selectedName={selected?.name ?? null}
                  onSelect={setSelected}
                />
              )}
            </div>

            <Group justify="space-between" mt="sm" wrap="nowrap">
              <Group gap="lg">
                {(['ready', 'booting', 'unknown', 'off'] as NodeState[]).map((s) => (
                  <Group key={s} gap={6}>
                    <div style={{
                      width: 10, height: 10, borderRadius: 3,
                      background: `var(--mantine-color-${STATE_COLOR[s]}-${s === 'off' ? 4 : 5})`,
                    }} />
                    <Text size="xs" c="dimmed">{READY_LABEL[s]}</Text>
                  </Group>
                ))}
              </Group>

              {selected ? (
                <Group gap="xs" wrap="nowrap">
                  <Text size="sm" fw={600}>{selected.name}</Text>
                  <Badge size="sm" variant="light" color={STATE_COLOR[stateOf(selected)]}>
                    {READY_LABEL[stateOf(selected)]}
                  </Badge>
                  <Text size="xs" c="dimmed">
                    {selected.image} · {selected.ram / 1024}GB · {selected.cpu} vCPU
                  </Text>
                  <Menu position="top-end">
                    <Menu.Target>
                      <Button size="compact-sm" variant="light">동작</Button>
                    </Menu.Target>
                    <Menu.Dropdown>
                      <Menu.Item
                        leftSection={<IconPlayerPlay size={14} />}
                        disabled={stateOf(selected) !== 'off'}
                        onClick={() => power('start', [selected.name])}
                      >
                        켜기
                      </Menu.Item>
                      <Menu.Item
                        leftSection={<IconPlayerStop size={14} />}
                        disabled={stateOf(selected) === 'off'}
                        onClick={() => power('stop', [selected.name])}
                      >
                        끄기
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
              <Group justify="space-between" mb="sm" wrap="nowrap">
                <Text fw={700} size="sm">진행 상황 · 테스트 결과</Text>
                <Button
                  size="compact-sm" variant="light"
                  leftSection={<IconListCheck size={14} />}
                  loading={checking} disabled={!lab || nodes.length === 0}
                  onClick={runCheck}
                >
                  점검 실행
                </Button>
              </Group>

              {/* 실행 결과가 있으면 그것을, 없으면 점검 결과나 안내를 보여준다 */}
              {run ? (
                <Stack gap="xs">
                  <Group gap="xs" justify="space-between" wrap="nowrap">
                    <Group gap="xs">
                      <Badge color={run.status === 'passed' ? 'teal'
                        : run.status === 'rolled_back' ? 'gray' : 'red'}>
                        {run.status === 'passed' ? '통과'
                          : run.status === 'failed' ? '실패'
                          : run.status === 'rolled_back' ? '롤백됨' : '오류'}
                      </Badge>
                      <Text size="xs" c="dimmed">#{run.id} {run.blueprint}</Text>
                      {/* 무엇을 재현한 실행인지. 케이스가 지워졌으면 나오지 않는다 */}
                      {run.case && (
                        <Button
                          size="compact-xs" variant="subtle"
                          rightSection={<IconExternalLink size={12} />}
                          onClick={() => router.push(`/cases/${run.case!.id}`)}
                        >
                          {run.case.case_id}
                        </Button>
                      )}
                    </Group>
                    <Group gap="xs">
                      {/* 되돌리지 않은 것이 장비에 남아 있으면 눈에 띄게 알린다 */}
                      {run.pending_rollback > 0 && (
                        <Button size="compact-xs" color="orange"
                                leftSection={<IconArrowBackUp size={13} />}
                                loading={running} onClick={doRollback}>
                          롤백 ({run.pending_rollback})
                        </Button>
                      )}
                      {/* 지식은 사람이 판단해 남긴다. 통과하지 못한 실행은
                          서버가 거절한다 — 실패에서 배운 것은 왜 안 됐는지가
                          기록에 없어서 돌려본 사람이 직접 적어야 한다. */}
                      <Button size="compact-xs" variant="light" color="grape"
                              leftSection={<IconBulb size={13} />}
                              loading={saving} onClick={saveKnowledge}>
                        지식으로 저장
                      </Button>
                      <Button size="compact-xs" variant="subtle" onClick={() => setRun(null)}>
                        닫기
                      </Button>
                    </Group>
                  </Group>
                  {saveResult && (
                    <Text size="xs" c={saveResult.ok ? 'teal' : 'red'}>
                      {saveResult.text}
                      {saveResult.ok && (
                        <Button size="compact-xs" variant="subtle" ml="xs"
                                onClick={() => router.push('/knowledge')}>
                          지식 베이스에서 보기
                        </Button>
                      )}
                    </Text>
                  )}
                  {run.pending_rollback > 0 && (
                    <Text size="xs" c="orange">
                      적용한 설정이 장비에 남아 있습니다. 롤백을 눌러 되돌리세요.
                    </Text>
                  )}
                  <ScrollArea.Autosize mah={240}>
                    <Stack gap={6}>
                      {run.steps.map((s) => (
                        <Group key={s.seq} gap="xs" align="flex-start" wrap="nowrap">
                          <div style={{ width: 16, paddingTop: 3 }}>
                            {s.status === 'pass' && <IconCheck size={14} color="var(--mantine-color-teal-6)" />}
                            {(s.status === 'fail' || s.status === 'error')
                              && <IconX size={14} color="var(--mantine-color-red-6)" />}
                            {s.status === 'skip' && (
                              <div style={{ width: 8, height: 8, borderRadius: 4, marginLeft: 3,
                                            border: '2px solid var(--mantine-color-gray-4)' }} />
                            )}
                          </div>
                          <div style={{ flex: 1, minWidth: 0 }}>
                            <Text size="xs" fw={600}
                                  c={s.status === 'fail' || s.status === 'error' ? 'red' : undefined}>
                              {s.phase} · {s.label}{s.node && ` · ${s.node}`}
                            </Text>
                            <Text size="xs" c="dimmed" style={{ wordBreak: 'break-word' }}>
                              {s.detail}
                            </Text>
                          </div>
                        </Group>
                      ))}
                    </Stack>
                  </ScrollArea.Autosize>
                </Stack>
              ) : check ? (
                <Stack gap="xs">
                  <Group gap="xs">
                    <Badge color="teal" variant="light">통과 {check.counts.pass}</Badge>
                    <Badge color={check.counts.fail ? 'red' : 'gray'} variant="light">
                      실패 {check.counts.fail}
                    </Badge>
                    <Badge color="gray" variant="light">건너뜀 {check.counts.skip}</Badge>
                  </Group>
                  <ScrollArea.Autosize mah={260}>
                    <Stack gap={6}>
                      {/* 실패를 먼저 보여준다 — 통과 목록을 스크롤해 찾게 하지 않는다 */}
                      {[...check.results].sort((a, b) =>
                        (a.status === 'fail' ? 0 : a.status === 'skip' ? 2 : 1)
                        - (b.status === 'fail' ? 0 : b.status === 'skip' ? 2 : 1)
                      ).map((r, i) => (
                        <Group key={i} gap="xs" align="flex-start" wrap="nowrap">
                          <div style={{ width: 16, paddingTop: 3 }}>
                            {r.status === 'pass' && <IconCheck size={14} color="var(--mantine-color-teal-6)" />}
                            {r.status === 'fail' && <IconX size={14} color="var(--mantine-color-red-6)" />}
                            {r.status === 'skip' && (
                              <div style={{ width: 8, height: 8, borderRadius: 4, marginLeft: 3,
                                            border: '2px solid var(--mantine-color-gray-4)' }} />
                            )}
                          </div>
                          <div style={{ flex: 1, minWidth: 0 }}>
                            <Text size="xs" fw={600} c={r.status === 'fail' ? 'red' : undefined}>
                              {r.node} · {r.check}
                            </Text>
                            <Text size="xs" c="dimmed" style={{ wordBreak: 'break-word' }}>
                              {r.detail}
                            </Text>
                          </div>
                        </Group>
                      ))}
                    </Stack>
                  </ScrollArea.Autosize>
                </Stack>
              ) : (
                <>
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
                    &ldquo;점검 실행&rdquo;은 장비에 붙어 hostname과 LLDP 배선을 대조합니다
                    (설정은 바꾸지 않습니다).
                  </Text>
                </>
              )}

              {blueprints.length > 0 && !run && (
                <>
                  <Divider my="sm" />
                  <Text size="xs" fw={700} c="dimmed" mb={6}>테스트 시나리오</Text>
                  {/* 무엇을 재현하려는 실행인지 남긴다. 비워두면 그냥 랩 테스트고,
                      채우면 결과가 그 케이스의 이력과 지식으로 돌아간다. */}
                  <Select
                    size="xs" mb={8} clearable searchable
                    placeholder="재현할 케이스 (선택)"
                    data={cases ?? []}
                    value={runCase}
                    onChange={setRunCase}
                    onDropdownOpen={loadCases}
                    nothingFoundMessage={cases === null ? '불러오는 중…' : '케이스 없음'}
                  />
                  <Stack gap={6}>
                    {blueprints.map((bp) => (
                      <Group key={bp.id} justify="space-between" gap="xs" wrap="nowrap">
                        <div style={{ minWidth: 0 }}>
                          <Text size="xs" fw={600}>{bp.name}</Text>
                          <Text size="xs" c={bp.problems.length ? 'orange' : 'dimmed'}>
                            {bp.problems.length
                              ? bp.problems[0]
                              : `${bp.steps}단계 · ${bp.description || '설명 없음'}`}
                          </Text>
                        </div>
                        <Button
                          size="compact-xs" variant="light"
                          leftSection={<IconPlayerTrackNext size={13} />}
                          loading={running}
                          disabled={bp.problems.length > 0}
                          onClick={() => startRun(bp.id)}
                        >
                          실행
                        </Button>
                      </Group>
                    ))}
                  </Stack>
                </>
              )}
            </Paper>

            <Paper withBorder radius="md" p="md"
                   style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
              <Text fw={700} size="sm" mb="sm">AI 대화</Text>
              <ScrollArea style={{ flex: 1 }} offsetScrollbars>
                <Stack gap="sm">
                  {chat.length === 0 && (
                    <Text size="sm" c="dimmed">
                      랩 상태·토폴로지를 묻거나 테스트를 요청하세요. 설정 변경은
                      제안으로만 나오고, 승인 버튼을 눌러야 장비에 적용됩니다.
                    </Text>
                  )}
                  {chat.map((m, i) => (
                    <div key={i}>
                      <Paper
                        p="sm" radius="md"
                        bg={m.role === 'user' ? 'blue.0' : 'gray.0'}
                        ml={m.role === 'user' ? 'xl' : 0}
                        mr={m.role === 'user' ? 0 : 'xl'}
                      >
                        <Text size="sm" style={{ whiteSpace: 'pre-wrap', lineHeight: 1.65 }}>
                          {m.text}
                        </Text>
                      </Paper>
                      {/* 실행 게이트 — 에이전트는 제안까지만 만든다. 이 버튼을
                          누르기 전에는 어떤 설정도 장비에 들어가지 않는다. */}
                      {m.proposals?.map((p) => (
                        <Card key={p.id} withBorder radius="md" p="sm" mt={6} mr="xl">
                          <Group justify="space-between" mb={6} wrap="nowrap">
                            <Text size="xs" fw={700}
                                  c={p.status === 'pending' ? 'orange'
                                    : p.status === 'approved' ? 'teal' : 'dimmed'}>
                              {p.status === 'pending' ? '승인 필요'
                                : p.status === 'approved' ? '승인됨' : '거절됨'} · {p.title}
                            </Text>
                          </Group>
                          {p.reason && <Text size="xs" c="dimmed" mb={6}>{p.reason}</Text>}
                          {p.steps.map((step, si) => (
                            <div key={si} style={{ marginBottom: 8 }}>
                              <Text size="xs" c="dimmed" mb={3}>
                                {step.role}{step.label && ` · ${step.label}`}
                              </Text>
                              <Paper bg="dark.8" p="xs" radius="sm">
                                <Text size="xs" c="gray.3" style={{
                                  fontFamily: 'var(--mantine-font-family-monospace)',
                                  whiteSpace: 'pre-wrap' }}>
                                  {step.apply.join('\n')}
                                </Text>
                              </Paper>
                              <Text size="xs" c="dimmed" mt={3}>
                                검증: {step.verify.command} → {step.verify.contains
                                  ? `'${step.verify.contains}' 포함`
                                  : `'${step.verify.not_contains}' 없음`}
                                {' · '}원복: {step.rollback.join('; ')}
                              </Text>
                            </div>
                          ))}
                          {p.status === 'pending' && (
                            <Group gap="xs">
                              <Button size="compact-sm" color="orange" loading={thinking}
                                      leftSection={<IconCheck size={14} />}
                                      onClick={() => decide(p, 'approve')}>
                                승인하고 적용
                              </Button>
                              <Button size="compact-sm" variant="default" loading={thinking}
                                      onClick={() => decide(p, 'reject')}>
                                거절
                              </Button>
                            </Group>
                          )}
                        </Card>
                      ))}
                    </div>
                  ))}
                  {thinking && (
                    <Group gap="xs"><Loader size="xs" />
                      <Text size="xs" c="dimmed">생각하는 중...</Text></Group>
                  )}
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

        {/* 검증된 명령 사전. 랩 밑이 아니라 옆에 둔다 — (벤더, OS 버전)이 키라
            어느 랩에서 확인됐든 같은 장비면 그대로 쓸모가 있다. */}
        <Modal opened={recipesOpen} onClose={() => setRecipesOpen(false)}
               title="검증된 명령 사전" size="xl">
          <Text size="sm" c="dimmed" mb="sm">
            랩에서 실제로 돌려본 명령 묶음입니다. 버전마다 되는 명령이 달라
            (벤더 · OS 버전)으로 모읍니다 — AI가 설정을 제안하기 전에 여기를 먼저 봅니다.
          </Text>
          <TextInput
            placeholder="목적이나 명령으로 검색 (예: description, bgp)"
            value={recipeQuery} mb="sm"
            onChange={(e) => setRecipeQuery(e.currentTarget.value)}
            onKeyDown={(e) => e.key === 'Enter' && loadRecipes(recipeQuery)}
            rightSection={
              <ActionIcon variant="subtle" onClick={() => loadRecipes(recipeQuery)}>
                <IconRefresh size={14} />
              </ActionIcon>
            }
          />
          <ScrollArea.Autosize mah={480}>
            {recipes === null ? (
              <Group justify="center" py="lg"><Loader size="sm" /></Group>
            ) : recipes.length === 0 ? (
              <Text size="sm" c="dimmed" py="lg" ta="center">
                아직 쌓인 것이 없습니다. 시나리오를 실행하면 그 결과가 여기 모입니다.
              </Text>
            ) : (
              <Stack gap="xs">
                {recipes.map((r) => (
                  <Card key={r.id} withBorder radius="md" p="sm">
                    <Group justify="space-between" wrap="nowrap" mb={6}>
                      <Group gap="xs" wrap="nowrap" style={{ minWidth: 0 }}>
                        <Badge size="sm" variant="light"
                               color={r.outcome === 'verified' ? 'teal'
                                 : r.outcome === 'untested' ? 'yellow' : 'red'}>
                          {r.outcome === 'verified' ? '검증됨'
                            : r.outcome === 'untested' ? '미검증' : '실패'}
                        </Badge>
                        <Text size="sm" fw={600} lineClamp={1}>{r.purpose}</Text>
                      </Group>
                      <Text size="xs" c="dimmed" style={{ whiteSpace: 'nowrap' }}>
                        {r.vendor} · {r.os_version || '버전 미상'}
                      </Text>
                    </Group>
                    <Text size="xs" ff="monospace" style={{ whiteSpace: 'pre-wrap' }}>
                      {r.apply.join('\n')}
                    </Text>
                    {r.verify.command && (
                      <Text size="xs" c="dimmed" ff="monospace" mt={4}>
                        확인: {r.verify.command}
                        {r.verify.contains && ` → "${r.verify.contains}" 포함`}
                      </Text>
                    )}
                    {/* 왜 안 되는지가 여기 남는다 — 같은 명령을 또 고르지 않게 */}
                    {r.outcome === 'failed' && r.last_failure && (
                      <Text size="xs" c="red" mt={4} lineClamp={2}>
                        {r.last_failure}
                      </Text>
                    )}
                    <Text size="xs" c="dimmed" mt={4}>
                      {r.source === 'manual' ? '직접 등록' : '랩 실행'}
                      {r.verified_count > 0 && ` · 통과 ${r.verified_count}회`}
                      {r.failed_count > 0 && ` · 실패 ${r.failed_count}회`}
                    </Text>
                  </Card>
                ))}
              </Stack>
            )}
          </ScrollArea.Autosize>
        </Modal>

        {/* 노드별 관리 접속 정보 — EVE-NG가 모르는 값이라 사람이 적는다.
            준비 판정(프로브)이 이 정보로 장비를 찌른다. 토폴로지 갱신으로
            덮이지 않는 별도 표다. */}
        <Modal opened={accessOpen} onClose={() => setAccessOpen(false)}
               title="노드 접속 정보" size="xl">
          <Stack gap="md">
            <Text size="sm" c="dimmed">
              EVE-NG는 장비의 관리 IP를 모릅니다(장비 설정 안에 있습니다).
              여기 적은 정보로 &ldquo;부팅이 끝났는지&rdquo;를 판정합니다.
              비워두면 그 노드는 <b>확인 불가</b>로 표시됩니다.
            </Text>

            {/* 겹침은 이 랩 안이 아니라 서버 전체 기준이다 — Community에서는
                랩들이 관리망(pnet0)을 공유해서 옆 랩이 쓰면 실제로 충돌한다.
                막지는 않는다: 랩은 일부러 이상한 값을 넣어보는 곳이기도 하다. */}
            {ipWarnings.length > 0 && (
              <Alert color="orange" variant="light"
                     icon={<IconAlertTriangle size={16} />}
                     title="IP를 확인하세요 (저장은 됩니다)">
                <Stack gap={2}>
                  {ipWarnings.map((w, i) => (
                    <Text size="xs" key={i}>
                      <b>{w.node}</b> {w.ip} — {w.message}
                    </Text>
                  ))}
                </Stack>
              </Alert>
            )}

            <Group gap="xs" align="flex-start" wrap="nowrap">
              <Text size="xs" c="dimmed" style={{ whiteSpace: 'nowrap', paddingTop: 3 }}>
                비어 있는 관리 IP:
              </Text>
              <Text size="xs" ff="monospace" style={{ flex: 1 }}>
                {freeIps.length > 0 ? freeIps.join(', ') : '풀에 남은 자리가 없습니다.'}
              </Text>
            </Group>

            <div style={{ overflowX: 'auto' }}>
              <Table>
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th style={{ minWidth: 110 }}>노드</Table.Th>
                    <Table.Th style={{ minWidth: 130 }}>역할</Table.Th>
                    <Table.Th style={{ minWidth: 140 }}>관리 IP</Table.Th>
                    <Table.Th style={{ minWidth: 150 }}>확인 방식</Table.Th>
                    <Table.Th style={{ minWidth: 110 }}>계정</Table.Th>
                    <Table.Th style={{ minWidth: 130 }}>비밀번호</Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {access.map((row, i) => {
                    const set = (patch: Partial<NodeAccess>) => setAccess(
                      (prev) => prev.map((r, j) => (i === j ? { ...r, ...patch } : r)));
                    return (
                      <Table.Tr key={row.node_name}>
                        <Table.Td><Text size="sm" fw={600}>{row.node_name}</Text></Table.Td>
                        <Table.Td>
                          <TextInput size="xs" placeholder="lb-primary"
                                     value={row.role}
                                     onChange={(e) => set({ role: e.currentTarget.value })} />
                        </Table.Td>
                        <Table.Td>
                          <TextInput size="xs" placeholder="192.168.74.151"
                                     value={row.mgmt_ip}
                                     onChange={(e) => set({ mgmt_ip: e.currentTarget.value })} />
                        </Table.Td>
                        <Table.Td>
                          <Select size="xs" data={DRIVERS} allowDeselect={false}
                                  value={row.driver}
                                  onChange={(v) => v && set({ driver: v })} />
                        </Table.Td>
                        <Table.Td>
                          <TextInput size="xs" value={row.username}
                                     onChange={(e) => set({ username: e.currentTarget.value })} />
                        </Table.Td>
                        <Table.Td>
                          {/* 저장된 비밀번호는 돌려받지 않는다. 비워두면 그대로 유지 */}
                          <TextInput size="xs" type="password"
                                     placeholder={row.has_password ? '저장됨 (변경 시 입력)' : ''}
                                     value={row.password ?? ''}
                                     onChange={(e) => set({ password: e.currentTarget.value })} />
                        </Table.Td>
                      </Table.Tr>
                    );
                  })}
                </Table.Tbody>
              </Table>
            </div>
            {/* 랩끼리 대역이 겹치면 서버를 공유하는 구조상 트래픽이 섞인다 */}
            <Group gap="xs" align="flex-end">
              <TextInput
                size="xs" label="시험 트래픽 대역" style={{ width: 180 }}
                placeholder={suggestedSubnet || '172.16.0.0/24'}
                value={dataSubnet}
                onChange={(e) => setDataSubnet(e.currentTarget.value)}
              />
              {suggestedSubnet && suggestedSubnet !== dataSubnet && (
                <Button size="compact-xs" variant="subtle"
                        onClick={() => setDataSubnet(suggestedSubnet)}>
                  비어 있는 {suggestedSubnet} 쓰기
                </Button>
              )}
            </Group>

            <Text size="xs" c="dimmed">
              비밀번호는 DB에 저장되고 화면으로 다시 내려오지 않습니다.
              DB 백업은 암호화되지만 DB 자체에는 평문으로 들어가므로,
              <b> 운영 장비 계정은 넣지 마세요.</b>
            </Text>
            <Group justify="flex-end" gap="xs">
              <Button variant="default" onClick={() => setAccessOpen(false)}>취소</Button>
              <Button leftSection={<IconCheck size={16} />} loading={savingAccess}
                      onClick={saveAccess}>
                저장
              </Button>
            </Group>
          </Stack>
        </Modal>

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

// useSearchParams는 Suspense 경계 안에서만 쓸 수 있다 (지식·케이스에서
// /labs?case=…, /labs?run=… 으로 건너오는 링크를 받기 위해 필요하다).
export default function LabsPage() {
  return (
    <Suspense fallback={<Center h="100vh"><Loader /></Center>}>
      <LabsPageInner />
    </Suspense>
  );
}
