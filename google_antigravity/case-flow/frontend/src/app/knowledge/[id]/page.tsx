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
import { apiFetch, apiUrl } from '../../lib/api';
import { useMe } from '../../lib/useMe';

// 커맨드/로그의 줄바꿈 유지 + 긴 문자열 강제 줄바꿈. 지식 본문은 명령어가 섞인
// 긴 글이라 줄간격을 기본값보다 넉넉히 준다 (읽기 어렵다는 피드백이 있었음).
const bodyTextStyle = {
  whiteSpace: 'pre-wrap',
  overflowWrap: 'anywhere',
  lineHeight: 1.75,
} as const;

// 본문 8칸의 표시 순서·라벨·설명. 보기 화면과 수정 폼이 같은 목록에서 나오므로
// 필드를 추가할 때 한 군데만 고치면 된다. 순서는 읽는 흐름 그대로 —
// 어떤 환경에서(환경) 무엇이 잘못됐고(문제) 어떻게 좁혀(진단) 원인을 찾아(원인)
// 무엇을 했고(해결) 어떻게 확인하며(검증) 무엇을 조심하는지(주의).
type SectionKey =
  | 'environment' | 'problem' | 'diagnosis' | 'root_cause'
  | 'resolution' | 'verification' | 'caveats' | 'related_refs';

interface Section {
  key: SectionKey;
  label: string;
  hint: string;
  rows: number;
  mono?: boolean;   // 명령어·식별자가 들어가는 칸은 고정폭 박스로
  tone?: 'warn';    // 주의사항은 경고 색 강조
}

const SECTIONS: Section[] = [
  { key: 'environment', label: '환경 · 전제 조건', hint: '구성 방식, 토폴로지, 장비 모델, 버전, 라이선스', rows: 3 },
  { key: 'problem', label: '문제 상황', hint: '어떤 조건에서 무엇이 잘못됐는지', rows: 4 },
  { key: 'diagnosis', label: '진단 절차', hint: '원인을 좁혀간 명령·로그·테스트를 순서대로', rows: 4 },
  { key: 'root_cause', label: '근본 원인', hint: '밝혀진 원인 (모르면 비워둡니다)', rows: 3 },
  { key: 'resolution', label: '해결 조치', hint: 'CLI 명령어·설정 라인·패치 버전을 그대로', rows: 6, mono: true },
  { key: 'verification', label: '검증 방법', hint: '조치 후 어떤 명령의 어떤 출력을 확인하는지', rows: 3, mono: true },
  { key: 'caveats', label: '주의사항', hint: '부작용, 재발 조건, 적용 범위 밖인 상황', rows: 3, tone: 'warn' },
  { key: 'related_refs', label: '관련 참조', hint: '벤더 버그 ID·케이스 번호·문서명 (한 줄에 하나)', rows: 2, mono: true },
];

const monoStyle = {
  ...bodyTextStyle,
  fontFamily: 'var(--mantine-font-family-monospace)',
  lineHeight: 1.65,
} as const;

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
  environment: string;
  problem: string;
  diagnosis: string;
  root_cause: string;
  resolution: string;
  verification: string;
  caveats: string;
  related_refs: string;
  device_model: string;
  software_version: string;
  status: string; // draft | confirmed
  analyzed_by: string;
  references: KnowledgeReference[];
  source_case: { id: number; case_id: string; status: string; vendor_case_number: string | null } | null;
  source_session: { id: number; title: string } | null;
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
      device_model: '',
      software_version: '',
      ...Object.fromEntries(SECTIONS.map((s) => [s.key, ''])),
    } as Record<string, string>,
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
          device_model: data.device_model || '',
          software_version: data.software_version || '',
          ...Object.fromEntries(SECTIONS.map((s) => [s.key, data[s.key] || ''])),
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

  const filledSections = SECTIONS.filter((s) => (item[s.key] || '').trim());
  const emptyKeys: SectionKey[] = SECTIONS
    .filter((s) => !(item[s.key] || '').trim())
    .map((s) => s.key);

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
                {/* AI가 못 채운 칸도 전부 노출한다 — 빈 칸을 엔지니어가 채워
                    완성해 가는 것이 이 화면의 목적이다 */}
                {SECTIONS.map((s) => (
                  <Textarea
                    key={s.key}
                    label={s.label}
                    description={s.hint}
                    placeholder={emptyKeys.includes(s.key) ? '아직 비어 있습니다 — 아는 내용을 채워주세요' : undefined}
                    autosize
                    minRows={s.rows}
                    styles={s.mono
                      ? { input: { fontFamily: 'var(--mantine-font-family-monospace)' } }
                      : undefined}
                    {...form.getInputProps(s.key)}
                  />
                ))}
              </Stack>
            ) : (
              <>
                <Title order={3} mb="lg">{item.title}</Title>

                <Stack gap="xl">
                  {filledSections.map((s) => (
                    <div
                      key={s.key}
                      style={{
                        borderLeft: `3px solid var(--mantine-color-${s.tone === 'warn' ? 'yellow' : 'blue'}-3)`,
                        paddingLeft: 'var(--mantine-spacing-md)',
                      }}
                    >
                      <Text fw={700} size="xs" c="dimmed" tt="uppercase" mb={8}
                            style={{ letterSpacing: '0.04em' }}>
                        {s.label}
                      </Text>
                      {s.mono ? (
                        <Paper bg="gray.0" p="md" radius="md">
                          <Text size="sm" style={monoStyle}>{item[s.key]}</Text>
                        </Paper>
                      ) : (
                        <Text size="sm" style={bodyTextStyle}>{item[s.key]}</Text>
                      )}
                    </div>
                  ))}

                  {/* 빈 칸을 감추면 무엇이 빠졌는지 아무도 모른다 — 채울 수 있는
                      사람에게만 한 줄로 알린다 */}
                  {canWrite && emptyKeys.length > 0 && (
                    <Text size="xs" c="dimmed">
                      비어 있는 항목: {SECTIONS.filter((s) => emptyKeys.includes(s.key))
                        .map((s) => s.label).join(' · ')}
                      {' — '}&quot;수정&quot;에서 채울 수 있습니다.
                    </Text>
                  )}

                  {item.references && item.references.length > 0 && (
                    <div style={{
                      borderLeft: '3px solid var(--mantine-color-teal-3)',
                      paddingLeft: 'var(--mantine-spacing-md)',
                    }}>
                      <Text fw={700} size="xs" c="dimmed" tt="uppercase" mb={8}
                            style={{ letterSpacing: '0.04em' }}>
                        공식 문서 근거
                      </Text>
                      <Stack gap="xs">
                        {item.references.map((ref, i) => (
                          <Paper key={i} withBorder p="sm" radius="md">
                            <Group justify="space-between" wrap="nowrap" align="flex-start">
                              <Text size="sm" fw={600}>
                                {ref.document} <Text component="span" c="dimmed">({ref.pages})</Text>
                              </Text>
                              <Button
                                component="a"
                                href={referenceFileUrl(ref)}
                                target="_blank"
                                size="compact-xs"
                                variant="light"
                                rightSection={<IconExternalLink size={12} />}
                                style={{ flexShrink: 0 }}
                              >
                                원본 열기
                              </Button>
                            </Group>
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
                {item.source_session ? (
                  <>
                    <Text size="sm" c="dimmed">출처:</Text>
                    {/* 대화 유래 지식은 벤더 해결 기록이 아닌 AI 답변 기반 —
                        케이스 유래보다 신뢰도가 낮음을 출처로 드러낸다 */}
                    <Text size="sm" fw={500} c="grape">
                      AI 도우미 대화 · {item.source_session.title}
                    </Text>
                  </>
                ) : (
                  <>
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
                  </>
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

// 인용 원본을 브라우저에서 열어 출처를 직접 확인하는 링크.
// PDF는 인용 시작 페이지로 바로 이동(#page=N), 엑셀은 브라우저 렌더링이
// 안 되므로 다운로드로 전환한다.
function referenceFileUrl(ref: KnowledgeReference) {
  const isPdf = ref.document.toLowerCase().endsWith('.pdf');
  const base = apiUrl(
    `/api/references/file/?path=${encodeURIComponent(ref.document)}${isPdf ? '' : '&dl=1'}`
  );
  const page = isPdf ? ref.pages.match(/p\.(\d+)/) : null;
  return page ? `${base}#page=${page[1]}` : base;
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
