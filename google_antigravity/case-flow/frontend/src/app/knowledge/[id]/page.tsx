'use client';

import { useState, useEffect } from 'react';
import { useParams, useRouter } from 'next/navigation';
import {
  AppShell,
  Container,
  Title,
  Text,
  Paper,
  Group,
  Badge,
  Button,
  Stack,
  Loader,
  Center,
  Divider,
  TextInput,
  Textarea,
} from '@mantine/core';
import { useForm } from '@mantine/form';
import {
  IconArrowLeft,
  IconEdit,
  IconDeviceFloppy,
  IconCircleCheck,
  IconTrash,
  IconExternalLink,
} from '@tabler/icons-react';
import ScrollToTopButton from '../../components/ScrollToTopButton';
import AppHeader from '../../components/AppHeader';
import { apiFetch } from '../../lib/api';
import { useMe } from '../../lib/useMe';

// 케이스 상세와 동일한 본문 스타일 — 커맨드/로그의 줄바꿈 유지 + 긴 문자열 강제 줄바꿈
const bodyTextStyle = { whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' } as const;

interface KnowledgeReference {
  document: string;
  pages: string;
  score: number;
  note: string;
}

interface KnowledgeDetail {
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
  references: KnowledgeReference[];
  source_case: { id: number; case_id: string; status: string; vendor_case_number: string | null } | null;
  created_at: string;
  updated_at: string;
}

export default function KnowledgeDetailPage() {
  const { id } = useParams();
  const router = useRouter();
  const [item, setItem] = useState<KnowledgeDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [isEditing, setIsEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState('');
  const { canWrite, isAdmin } = useMe();

  const form = useForm({
    initialValues: {
      title: '',
      problem: '',
      root_cause: '',
      resolution: '',
      device_model: '',
      software_version: '',
    },
  });

  const loadItem = () => {
    apiFetch(`/api/knowledge/${id}/`)
      .then((res) => {
        if (res.ok) return res.json();
        throw new Error('Failed to fetch knowledge item');
      })
      .then((data) => {
        setItem(data);
        form.setValues({
          title: data.title,
          problem: data.problem,
          root_cause: data.root_cause || '',
          resolution: data.resolution,
          device_model: data.device_model || '',
          software_version: data.software_version || '',
        });
      })
      .catch((err) => console.error(err))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    loadItem();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  const patch = async (payload: Record<string, string>, doneMessage: string) => {
    setSaving(true);
    setMessage('');
    try {
      const res = await apiFetch(`/api/knowledge/${id}/`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (res.ok) {
        setItem(await res.json());
        setMessage(doneMessage);
        setIsEditing(false);
      } else {
        const data = await res.json().catch(() => ({}));
        setMessage(`저장 실패: ${data.error || res.statusText}`);
      }
    } catch {
      setMessage('저장 실패: 백엔드 서버에 연결할 수 없습니다.');
    } finally {
      setSaving(false);
    }
  };

  const handleSave = () => patch(form.values, '저장되었습니다.');
  // 수정 상태에서 확정을 누르면 편집 내용도 함께 저장된다
  const handleConfirm = () =>
    patch({ ...(isEditing ? form.values : {}), status: 'confirmed' },
      '확정되었습니다. 이제 헬프 에이전트 검색에서 검증된 지식으로 우선 노출됩니다.');

  const handleDelete = async () => {
    if (!window.confirm(`${item?.knowledge_id} 지식 항목을 삭제할까요? 되돌릴 수 없습니다.`)) return;
    const res = await apiFetch(`/api/knowledge/${id}/`, { method: 'DELETE' });
    if (res.ok) {
      router.push('/knowledge');
    } else {
      setMessage('삭제에 실패했습니다.');
    }
  };

  if (loading) {
    return (
      <Center h="100vh">
        <Loader size="lg" />
      </Center>
    );
  }

  if (!item) {
    return (
      <Center h="100vh">
        <Stack align="center">
          <Text>지식 항목을 찾을 수 없습니다.</Text>
          <Button onClick={() => router.push('/knowledge')}>목록으로</Button>
        </Stack>
      </Center>
    );
  }

  return (
    <AppShell header={{ height: 60 }} padding="md">
      <AppShell.Header>
        <AppHeader />
      </AppShell.Header>

      <AppShell.Main>
        <Container size="md">
          <Group justify="space-between" mb="lg">
            <Button
              variant="subtle"
              leftSection={<IconArrowLeft size={16} />}
              onClick={() => router.push('/knowledge')}
            >
              목록으로
            </Button>
            <Group gap="xs">
              {canWrite && !isEditing && (
                <Button
                  variant="default"
                  leftSection={<IconEdit size={16} />}
                  onClick={() => setIsEditing(true)}
                >
                  수정
                </Button>
              )}
              {canWrite && isEditing && (
                <Button
                  leftSection={<IconDeviceFloppy size={16} />}
                  loading={saving}
                  onClick={handleSave}
                >
                  저장
                </Button>
              )}
              {canWrite && item.status === 'draft' && (
                <Button
                  color="green"
                  leftSection={<IconCircleCheck size={16} />}
                  loading={saving}
                  onClick={handleConfirm}
                >
                  확정
                </Button>
              )}
              {isAdmin && (
                <Button
                  color="red"
                  variant="light"
                  leftSection={<IconTrash size={16} />}
                  onClick={handleDelete}
                >
                  삭제
                </Button>
              )}
            </Group>
          </Group>

          {message && (
            <Text size="sm" c={message.includes('실패') ? 'red' : 'teal'} mb="sm">
              {message}
            </Text>
          )}

          <Paper shadow="xs" p="lg" withBorder>
            <Group gap="xs" mb="xs">
              <Text fw={700} c="dimmed">{item.knowledge_id}</Text>
              <Badge color={getVendorColor(item.vendor)} variant="light">{item.vendor}</Badge>
              <Badge color={item.status === 'confirmed' ? 'green' : 'yellow'} variant="dot">
                {item.status === 'confirmed' ? '확정' : 'AI 초안'}
              </Badge>
              {item.device_model && (
                <Badge variant="outline" color="gray">
                  {item.device_model}{item.software_version && ` · v${item.software_version}`}
                </Badge>
              )}
            </Group>

            {isEditing ? (
              <Stack gap="md">
                <TextInput label="문제 요약" {...form.getInputProps('title')} />
                <Group grow>
                  <TextInput label="장비 모델" {...form.getInputProps('device_model')} />
                  <TextInput label="소프트웨어 버전" {...form.getInputProps('software_version')} />
                </Group>
                <Textarea label="문제 상황" autosize minRows={3} {...form.getInputProps('problem')} />
                <Textarea label="근본 원인" autosize minRows={2} {...form.getInputProps('root_cause')} />
                <Textarea label="해결 조치" autosize minRows={5} {...form.getInputProps('resolution')} />
              </Stack>
            ) : (
              <>
                <Title order={3} mb="lg">{item.title}</Title>

                <Stack gap="lg">
                  <div>
                    <Text fw={700} size="sm" c="dimmed" mb={4}>문제 상황</Text>
                    <Text size="sm" style={bodyTextStyle}>{item.problem}</Text>
                  </div>
                  {item.root_cause && (
                    <div>
                      <Text fw={700} size="sm" c="dimmed" mb={4}>근본 원인</Text>
                      <Text size="sm" style={bodyTextStyle}>{item.root_cause}</Text>
                    </div>
                  )}
                  <div>
                    <Text fw={700} size="sm" c="dimmed" mb={4}>해결 조치</Text>
                    <Paper bg="gray.0" p="md" radius="md">
                      <Text size="sm" style={{ ...bodyTextStyle, fontFamily: 'var(--mantine-font-family-monospace)' }}>
                        {item.resolution}
                      </Text>
                    </Paper>
                  </div>
                  {item.references && item.references.length > 0 && (
                    <div>
                      <Text fw={700} size="sm" c="dimmed" mb={4}>공식 문서 근거</Text>
                      <Stack gap="xs">
                        {item.references.map((ref, i) => (
                          <Paper key={i} withBorder p="sm" radius="md">
                            <Text size="sm" fw={600}>
                              {ref.document} <Text component="span" c="dimmed">({ref.pages})</Text>
                            </Text>
                            <Text size="sm" c="dimmed">{ref.note}</Text>
                          </Paper>
                        ))}
                      </Stack>
                    </div>
                  )}
                </Stack>
              </>
            )}

            <Divider my="lg" />

            <Group justify="space-between">
              <Group gap="xs">
                <Text size="sm" c="dimmed">출처 케이스:</Text>
                {item.source_case ? (
                  <Button
                    size="compact-sm"
                    variant="light"
                    rightSection={<IconExternalLink size={14} />}
                    onClick={() => router.push(`/cases/${item.source_case!.id}`)}
                  >
                    {item.source_case.case_id}
                    {item.source_case.vendor_case_number && ` (${item.source_case.vendor_case_number})`}
                  </Button>
                ) : (
                  <Text size="sm" c="dimmed">삭제됨</Text>
                )}
              </Group>
              <Text size="xs" c="dimmed">
                {item.analyzed_by && `추출 모델: ${item.analyzed_by} · `}
                등록 {item.created_at.slice(0, 10)}
              </Text>
            </Group>
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
