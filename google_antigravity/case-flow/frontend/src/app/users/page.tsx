'use client';

import { useCallback, useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import {
  AppShell,
  Badge,
  Button,
  Center,
  Container,
  Group,
  Loader,
  Modal,
  Paper,
  PasswordInput,
  Select,
  Stack,
  Switch,
  Table,
  Text,
  TextInput,
  Title,
} from '@mantine/core';
import { useForm } from '@mantine/form';
import { IconBulb, IconKey, IconMailDown, IconPlus, IconTrash, IconUserCheck, IconUserOff } from '@tabler/icons-react';
import AppHeader from '../components/AppHeader';
import { apiFetch } from '../lib/api';
import { ROLE_LABELS, Role, useMe } from '../lib/useMe';

interface SyncSchedule {
  enabled: boolean;
  last_run: string;
  schedule: string;
}

interface KnowledgeModelSetting {
  current: string;
  default: string;
  models: { id: string; note: string; key_configured: boolean }[];
}

// 모델 id를 사람이 읽는 이름으로. 서버가 주는 목록은 두 개뿐이라 여기만 맞추면 된다.
const MODEL_LABELS: Record<string, string> = {
  'claude-opus-5': 'Opus 5',
  'claude-sonnet-5': 'Sonnet 5',
};

interface Account {
  id: number;
  username: string;
  name: string;
  role: Role;
  is_active: boolean;
  last_login: string | null;
  date_joined: string;
}

const ROLE_SELECT_DATA = (Object.keys(ROLE_LABELS) as Role[]).map((r) => ({
  value: r,
  label: ROLE_LABELS[r],
}));

export default function UsersPage() {
  const router = useRouter();
  const { me } = useMe();
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState('');
  const [createOpened, setCreateOpened] = useState(false);
  const [resetTarget, setResetTarget] = useState<Account | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<Account | null>(null);
  const [saving, setSaving] = useState(false);

  const fetchAccounts = useCallback(async () => {
    try {
      const response = await apiFetch('/api/auth/users/');
      if (response.ok) {
        setAccounts(await response.json());
      } else if (response.status === 403) {
        // 관리자가 아니면 목록으로
        router.push('/');
      }
    } catch (error) {
      console.error('Error fetching accounts:', error);
    } finally {
      setLoading(false);
    }
  }, [router]);

  useEffect(() => {
    fetchAccounts();
  }, [fetchAccounts]);

  // Gmail 자동 수집 스위치 — VM cron은 계속 돌고, 이 값이 꺼져 있으면 수집을 건너뛴다
  const [schedule, setSchedule] = useState<SyncSchedule | null>(null);
  const [scheduleSaving, setScheduleSaving] = useState(false);

  useEffect(() => {
    apiFetch('/api/settings/gmail-sync/')
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => data && setSchedule(data))
      .catch(() => {});
  }, []);

  // 지식 추출 모델 — 메일 분석 모델과 별개다. 지식은 케이스당 1회 만들어
  // 오래 재사용하는 자산이라 품질 우선이고, 선택지도 상위 두 모델로 제한된다.
  const [knowledgeModel, setKnowledgeModel] = useState<KnowledgeModelSetting | null>(null);
  const [knowledgeModelSaving, setKnowledgeModelSaving] = useState(false);

  useEffect(() => {
    apiFetch('/api/settings/knowledge-model/')
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => data && setKnowledgeModel(data))
      .catch(() => {});
  }, []);

  const handleKnowledgeModelChange = async (model: string | null) => {
    if (!model || model === knowledgeModel?.current) return;
    setKnowledgeModelSaving(true);
    try {
      const response = await apiFetch('/api/settings/knowledge-model/', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model }),
      });
      const data = await response.json();
      if (response.ok) {
        setKnowledgeModel(data);
        setMessage(`지식 추출 모델을 ${MODEL_LABELS[data.current] ?? data.current}(으)로 변경했습니다.`);
      } else {
        setMessage(data.error || '지식 추출 모델 변경에 실패했습니다.');
      }
    } catch {
      setMessage('백엔드 서버에 연결할 수 없습니다.');
    } finally {
      setKnowledgeModelSaving(false);
    }
  };

  const handleScheduleToggle = async (enabled: boolean) => {
    setScheduleSaving(true);
    try {
      const response = await apiFetch('/api/settings/gmail-sync/', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled }),
      });
      if (response.ok) {
        setSchedule(await response.json());
        setMessage(enabled ? '자동 수집을 켰습니다.' : '자동 수집을 껐습니다.');
      } else {
        setMessage('자동 수집 설정 변경에 실패했습니다.');
      }
    } catch {
      setMessage('백엔드 서버에 연결할 수 없습니다.');
    } finally {
      setScheduleSaving(false);
    }
  };

  const createForm = useForm({
    initialValues: { username: '', name: '', password: '', role: 'viewer' as Role },
    validate: {
      username: (v) => (v.trim() ? null : '아이디를 입력하세요'),
      password: (v) => (v.length < 8 ? '비밀번호는 8자 이상이어야 합니다' : null),
    },
  });

  const resetForm = useForm({
    initialValues: { password: '' },
    validate: {
      password: (v) => (v.length < 8 ? '비밀번호는 8자 이상이어야 합니다' : null),
    },
  });

  const handleCreate = async (values: typeof createForm.values) => {
    setSaving(true);
    try {
      const response = await apiFetch('/api/auth/users/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(values),
      });
      const data = await response.json();
      if (response.ok) {
        setMessage(`계정이 발급되었습니다: ${data.username}`);
        createForm.reset();
        setCreateOpened(false);
        fetchAccounts();
      } else {
        createForm.setFieldError('username', data.error || '계정 발급에 실패했습니다.');
      }
    } catch {
      createForm.setFieldError('username', '백엔드 서버에 연결할 수 없습니다.');
    } finally {
      setSaving(false);
    }
  };

  const handleRoleChange = async (account: Account, role: string | null) => {
    if (!role || role === account.role) return;
    const response = await apiFetch(`/api/auth/users/${account.id}/`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ role }),
    });
    const data = await response.json();
    if (response.ok) {
      setMessage(`${account.username}의 역할을 ${ROLE_LABELS[role as Role]}(으)로 변경했습니다.`);
      fetchAccounts();
    } else {
      setMessage(data.error || '역할 변경에 실패했습니다.');
    }
  };

  const handleToggleActive = async (account: Account) => {
    const response = await apiFetch(`/api/auth/users/${account.id}/`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ is_active: !account.is_active }),
    });
    const data = await response.json();
    if (response.ok) {
      setMessage(
        data.is_active
          ? `${account.username} 계정을 활성화했습니다.`
          : `${account.username} 계정을 비활성화했습니다. 더 이상 로그인할 수 없습니다.`
      );
      fetchAccounts();
    } else {
      setMessage(data.error || '변경에 실패했습니다.');
    }
  };

  const handleResetPassword = async (values: typeof resetForm.values) => {
    if (!resetTarget) return;
    setSaving(true);
    try {
      const response = await apiFetch(`/api/auth/users/${resetTarget.id}/`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password: values.password }),
      });
      const data = await response.json();
      if (response.ok) {
        setMessage(`${resetTarget.username}의 비밀번호가 재설정되었습니다.`);
        resetForm.reset();
        setResetTarget(null);
      } else {
        resetForm.setFieldError('password', data.error || '재설정에 실패했습니다.');
      }
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    if (!deleteTarget) return;
    setSaving(true);
    try {
      const response = await apiFetch(`/api/auth/users/${deleteTarget.id}/`, { method: 'DELETE' });
      if (response.ok) {
        setMessage(`${deleteTarget.username} 계정을 삭제했습니다.`);
        fetchAccounts();
      } else {
        const data = await response.json();
        setMessage(data.error || '삭제에 실패했습니다.');
      }
      setDeleteTarget(null);
    } finally {
      setSaving(false);
    }
  };

  const rows = accounts.map((account) => (
    <Table.Tr key={account.id} opacity={account.is_active ? 1 : 0.5}>
      <Table.Td>
        <Text fw={500}>{account.username}</Text>
        {account.name && <Text size="xs" c="dimmed">{account.name}</Text>}
      </Table.Td>
      <Table.Td>
        <Select
          data={ROLE_SELECT_DATA}
          value={account.role}
          onChange={(v) => handleRoleChange(account, v)}
          size="xs"
          w={110}
          allowDeselect={false}
        />
      </Table.Td>
      <Table.Td>
        <Badge variant="dot" color={account.is_active ? 'green' : 'gray'}>
          {account.is_active ? '활성' : '비활성'}
        </Badge>
      </Table.Td>
      <Table.Td>
        <Text size="sm">{account.last_login || '로그인 이력 없음'}</Text>
      </Table.Td>
      <Table.Td>
        <Text size="sm">{account.date_joined}</Text>
      </Table.Td>
      <Table.Td>
        <Group gap="xs" justify="flex-end">
          <Button
            size="xs"
            variant="light"
            leftSection={<IconKey size={14} />}
            onClick={() => setResetTarget(account)}
          >
            비밀번호 재설정
          </Button>
          <Button
            size="xs"
            variant="light"
            color={account.is_active ? 'red' : 'teal'}
            leftSection={account.is_active ? <IconUserOff size={14} /> : <IconUserCheck size={14} />}
            onClick={() => handleToggleActive(account)}
          >
            {account.is_active ? '비활성화' : '활성화'}
          </Button>
          {/* 자기 자신은 삭제 버튼 숨김 (서버도 차단) */}
          {account.username !== me?.username && (
            <Button
              size="xs"
              variant="subtle"
              color="red"
              leftSection={<IconTrash size={14} />}
              onClick={() => setDeleteTarget(account)}
            >
              삭제
            </Button>
          )}
        </Group>
      </Table.Td>
    </Table.Tr>
  ));

  return (
    <AppShell header={{ height: 60 }} padding="md">
      <AppShell.Header>
        <AppHeader />
      </AppShell.Header>

      <AppShell.Main>
        <Container size="lg">
          <Group justify="space-between" mb="lg">
            <div>
              <Title order={2}>계정 관리</Title>
              <Text c="dimmed">사용자 계정 발급 및 관리 (관리자 전용)</Text>
            </div>
            <Button leftSection={<IconPlus size={14} />} onClick={() => setCreateOpened(true)}>
              새 계정 발급
            </Button>
          </Group>

          {message && (
            <Text size="sm" c="teal" mb="sm">
              {message}
            </Text>
          )}

          {schedule && (
            <Paper shadow="xs" p="md" withBorder mb="lg">
              <Group justify="space-between" wrap="nowrap">
                <Group gap="sm" wrap="nowrap">
                  <IconMailDown size={20} />
                  <div>
                    <Text fw={600}>Gmail 자동 수집</Text>
                    <Text size="sm" c="dimmed">
                      {schedule.schedule}에 벤더 케이스 메일을 자동으로 가져옵니다.
                      끄면 수집이 멈추고, 케이스 목록의 &quot;Gmail 동기화&quot; 버튼으로는
                      계속 수동 수집할 수 있습니다.
                    </Text>
                    <Text size="xs" c="dimmed" mt={4}>
                      마지막 수집: {schedule.last_run || '기록 없음'}
                    </Text>
                  </div>
                </Group>
                <Switch
                  size="md"
                  checked={schedule.enabled}
                  disabled={scheduleSaving}
                  onChange={(event) => handleScheduleToggle(event.currentTarget.checked)}
                  label={schedule.enabled ? '켜짐' : '꺼짐'}
                  labelPosition="left"
                />
              </Group>
            </Paper>
          )}

          {knowledgeModel && (
            <Paper shadow="xs" p="md" withBorder mb="lg">
              <Group justify="space-between" wrap="nowrap" align="flex-start">
                <Group gap="sm" wrap="nowrap">
                  <IconBulb size={20} />
                  <div>
                    <Text fw={600}>지식 추출 AI 모델</Text>
                    <Text size="sm" c="dimmed">
                      해결된 케이스와 AI 도우미 대화에서 지식 초안을 정리하는 모델입니다.
                      메일 번역·분석 모델(케이스 목록에서 선택)과는 별개로 동작합니다.
                    </Text>
                    <Text size="xs" c="dimmed" mt={4}>
                      지식은 한 번 만들어 오래 재사용하므로 품질을 우선합니다.
                      Opus 5가 더 자세하고, Sonnet 5는 비용이 약 40% 수준입니다.
                    </Text>
                  </div>
                </Group>
                <Select
                  w={180}
                  allowDeselect={false}
                  value={knowledgeModel.current}
                  disabled={knowledgeModelSaving}
                  onChange={handleKnowledgeModelChange}
                  data={knowledgeModel.models.map((m) => ({
                    value: m.id,
                    label: `${MODEL_LABELS[m.id] ?? m.id} · ${m.note.split('—')[0].trim()}`,
                    disabled: !m.key_configured,
                  }))}
                />
              </Group>
            </Paper>
          )}

          <Paper shadow="xs" p="md" withBorder>
            {loading ? (
              <Center py="xl">
                <Loader size="lg" />
              </Center>
            ) : (
              <Table highlightOnHover verticalSpacing="sm">
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>계정</Table.Th>
                    <Table.Th style={{ width: 120 }}>역할</Table.Th>
                    <Table.Th style={{ width: 90 }}>상태</Table.Th>
                    <Table.Th style={{ width: 150 }}>마지막 로그인</Table.Th>
                    <Table.Th style={{ width: 110 }}>생성일</Table.Th>
                    <Table.Th style={{ width: 310 }} />
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>{rows}</Table.Tbody>
              </Table>
            )}
          </Paper>
        </Container>
      </AppShell.Main>

      <Modal
        opened={createOpened}
        onClose={() => setCreateOpened(false)}
        title="새 계정 발급"
        centered
      >
        <form onSubmit={createForm.onSubmit(handleCreate)}>
          <Stack>
            <TextInput
              required
              label="아이디"
              placeholder="로그인에 사용할 아이디"
              {...createForm.getInputProps('username')}
            />
            <TextInput
              label="이름"
              placeholder="사용자 이름 (선택)"
              {...createForm.getInputProps('name')}
            />
            <PasswordInput
              required
              label="초기 비밀번호"
              placeholder="8자 이상, 숫자만은 불가"
              {...createForm.getInputProps('password')}
            />
            <Select
              label="역할"
              data={ROLE_SELECT_DATA}
              allowDeselect={false}
              description="조회자: 열람만 · 엔지니어: 케이스 조작 · 관리자: 삭제/설정/계정 관리"
              {...createForm.getInputProps('role')}
            />
            <Button type="submit" loading={saving} fullWidth>
              발급
            </Button>
          </Stack>
        </form>
      </Modal>

      <Modal
        opened={deleteTarget !== null}
        onClose={() => setDeleteTarget(null)}
        title={`계정 삭제 — ${deleteTarget?.username ?? ''}`}
        centered
      >
        <Stack>
          <Text size="sm">
            <Text span fw={600}>{deleteTarget?.username}</Text>
            {deleteTarget?.name ? ` (${deleteTarget.name})` : ''} 계정을 완전히 삭제합니다.
            이 작업은 되돌릴 수 없습니다.
          </Text>
          <Text size="xs" c="dimmed">
            나중에 다시 사용할 가능성이 있다면 삭제 대신 비활성화를 권장합니다.
          </Text>
          <Group justify="flex-end">
            <Button variant="default" onClick={() => setDeleteTarget(null)}>
              취소
            </Button>
            <Button color="red" loading={saving} onClick={handleDelete}>
              삭제
            </Button>
          </Group>
        </Stack>
      </Modal>

      <Modal
        opened={resetTarget !== null}
        onClose={() => setResetTarget(null)}
        title={`비밀번호 재설정 — ${resetTarget?.username ?? ''}`}
        centered
      >
        <form onSubmit={resetForm.onSubmit(handleResetPassword)}>
          <Stack>
            <PasswordInput
              required
              label="새 비밀번호"
              placeholder="8자 이상, 숫자만은 불가"
              {...resetForm.getInputProps('password')}
            />
            <Button type="submit" loading={saving} fullWidth>
              재설정
            </Button>
          </Stack>
        </form>
      </Modal>
    </AppShell>
  );
}
