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
  Table,
  Text,
  TextInput,
  Title,
} from '@mantine/core';
import { useForm } from '@mantine/form';
import { IconKey, IconPlus, IconUserCheck, IconUserOff } from '@tabler/icons-react';
import AppHeader from '../components/AppHeader';
import { apiFetch } from '../lib/api';
import { ROLE_LABELS, Role } from '../lib/useMe';

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
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState('');
  const [createOpened, setCreateOpened] = useState(false);
  const [resetTarget, setResetTarget] = useState<Account | null>(null);
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
                    <Table.Th style={{ width: 240 }} />
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
