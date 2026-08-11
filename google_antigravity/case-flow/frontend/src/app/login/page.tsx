'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import {
  Anchor,
  Button,
  Center,
  Modal,
  Paper,
  PasswordInput,
  SegmentedControl,
  Stack,
  Text,
  Textarea,
  TextInput,
  Title,
} from '@mantine/core';
import { useForm } from '@mantine/form';
import { IconLock, IconUser } from '@tabler/icons-react';
import { apiFetch } from '../lib/api';

export default function LoginPage() {
  const router = useRouter();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [signupOpened, setSignupOpened] = useState(false);
  const [signupSaving, setSignupSaving] = useState(false);
  const [notice, setNotice] = useState('');

  const form = useForm({
    initialValues: { username: '', password: '' },
    validate: {
      username: (v) => (v.trim() ? null : '아이디를 입력하세요'),
      password: (v) => (v ? null : '비밀번호를 입력하세요'),
    },
  });

  const signupForm = useForm({
    initialValues: { username: '', name: '', email: '', password: '', reason: '', requested_role: 'viewer' },
    validate: {
      username: (v) => (v.trim() ? null : '아이디를 입력하세요'),
      email: (v) => (/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v.trim()) ? null : '메일 주소를 입력하세요'),
      password: (v) => (v.length < 8 ? '비밀번호는 8자 이상이어야 합니다' : null),
    },
  });

  const handleSubmit = async (values: typeof form.values) => {
    setLoading(true);
    setError('');
    try {
      const response = await apiFetch('/api/auth/login/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(values),
      });
      if (response.ok) {
        router.push('/');
      } else {
        const data = await response.json().catch(() => null);
        setError(data?.error || '로그인에 실패했습니다.');
      }
    } catch {
      setError('백엔드 서버에 연결할 수 없습니다.');
    } finally {
      setLoading(false);
    }
  };

  const handleSignup = async (values: typeof signupForm.values) => {
    setSignupSaving(true);
    try {
      const response = await apiFetch('/api/auth/signup-requests/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(values),
      });
      const data = await response.json().catch(() => null);
      if (response.ok) {
        signupForm.reset();
        setSignupOpened(false);
        setNotice('계정이 생성되었습니다. 방금 입력한 아이디/비밀번호로 바로 로그인하세요.');
      } else {
        signupForm.setFieldError('username', data?.error || '요청 접수에 실패했습니다.');
      }
    } catch {
      signupForm.setFieldError('username', '백엔드 서버에 연결할 수 없습니다.');
    } finally {
      setSignupSaving(false);
    }
  };

  return (
    <Center h="100vh" bg="gray.0">
      <Paper shadow="md" p="xl" withBorder w={380}>
        <Stack>
          <div>
            <Title order={2}>Case-Flow</Title>
            <Text c="dimmed" size="sm">
              벤더 TAC 케이스 관리 시스템
            </Text>
          </div>
          {notice && (
            <Text c="teal" size="sm">
              {notice}
            </Text>
          )}
          <form onSubmit={form.onSubmit(handleSubmit)}>
            <Stack>
              <TextInput
                label="아이디"
                placeholder="발급받은 계정"
                leftSection={<IconUser size={14} />}
                autoComplete="username"
                {...form.getInputProps('username')}
              />
              <PasswordInput
                label="비밀번호"
                placeholder="비밀번호"
                leftSection={<IconLock size={14} />}
                autoComplete="current-password"
                {...form.getInputProps('password')}
              />
              {error && (
                <Text c="red" size="sm">
                  {error}
                </Text>
              )}
              <Button type="submit" loading={loading} fullWidth mt="xs">
                로그인
              </Button>
              <Text c="dimmed" size="xs" ta="center">
                계정이 없나요?{' '}
                <Anchor size="xs" onClick={() => setSignupOpened(true)}>
                  계정 발급 요청
                </Anchor>
              </Text>
            </Stack>
          </form>
        </Stack>
      </Paper>

      <Modal
        opened={signupOpened}
        onClose={() => setSignupOpened(false)}
        title="계정 발급 요청"
        centered
      >
        <form onSubmit={signupForm.onSubmit(handleSignup)}>
          <Stack>
            <Text size="sm" c="dimmed">
              입력하시면 계정이 바로 만들어져 곧장 로그인하실 수 있습니다.
              가입 사실은 관리자에게 알림으로 전달됩니다.
            </Text>
            <TextInput
              required
              label="사용할 아이디"
              placeholder="예: hgildong"
              {...signupForm.getInputProps('username')}
            />
            <TextInput
              label="이름"
              placeholder="홍길동"
              {...signupForm.getInputProps('name')}
            />
            <TextInput
              required
              label="사내 메일"
              placeholder="hgildong@ubersys.co.kr"
              description="공지나 문의 회신에만 사용합니다"
              {...signupForm.getInputProps('email')}
            />
            <PasswordInput
              required
              label="사용할 비밀번호"
              placeholder="8자 이상, 숫자만은 불가"
              {...signupForm.getInputProps('password')}
            />
            <div>
              <Text size="sm" fw={500} mb={4}>
                신청 역할
              </Text>
              <SegmentedControl
                fullWidth
                data={[
                  { label: '조회자 (viewer)', value: 'viewer' },
                  { label: '엔지니어 (engineer)', value: 'engineer' },
                ]}
                {...signupForm.getInputProps('requested_role')}
              />
              <Text size="xs" c="dimmed" mt={4}>
                조회자는 조회만, 엔지니어는 케이스 등록·수정과 AI 도우미까지 사용할 수 있습니다.
                관리자 권한은 별도 요청해 주세요.
              </Text>
            </div>
            <Textarea
              label="요청 사유"
              placeholder="예: OO팀, 케이스 조회 필요 (선택)"
              minRows={2}
              {...signupForm.getInputProps('reason')}
            />
            <Button type="submit" loading={signupSaving} fullWidth>
              발급 요청 보내기
            </Button>
          </Stack>
        </form>
      </Modal>
    </Center>
  );
}
