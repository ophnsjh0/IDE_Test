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
  Select,
  Modal,
} from '@mantine/core';
import { useForm } from '@mantine/form';
import {
  IconArrowLeft,
  IconEdit,
  IconDeviceFloppy,
  IconMailDown,
  IconMailUp,
  IconLanguage,
  IconBulb,
  IconTrash,
} from '@tabler/icons-react'; // IconDeviceFloppy for Save
import ScrollToTopButton from '../../components/ScrollToTopButton';
import AppHeader from '../../components/AppHeader';
import { apiFetch } from '../../lib/api';
import { useMe } from '../../lib/useMe';

// 본문 텍스트 공통 스타일 — AI 분석 결과와 메일 본문에 동일하게 적용.
// overflowWrap: 긴 URL·시리얼 등 공백 없는 문자열이 카드 밖으로 넘치지 않게 강제 줄바꿈
const bodyTextStyle = { whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' } as const;

interface CaseEmail {
  id: number;
  direction: string;
  sender: string;
  recipient: string;
  subject: string;
  subject_ko: string;
  body_original: string;
  body_ko: string;
  received_at: string;
}

interface RelatedCase {
  id: number;
  case_id: string;
  vendor: string;
  status: string;
  summary: string;
  vendor_case_number: string | null;
}

interface CaseDetail {
  id: number;
  case_id: string;
  vendor: string;
  status: string;
  summary: string;
  description: string;
  action_steps: string;
  resolution: string;
  source: string;
  analyzed_by: string;
  vendor_case_number: string | null;
  device_model: string;
  device_serial: string;
  software_version: string;
  date: string;
  created_at: string;
  emails: CaseEmail[];
  related_cases: RelatedCase[];
}

export default function CaseDetailPage() {
  const { id } = useParams();
  const router = useRouter();
  const [caseDetail, setCaseDetail] = useState<CaseDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [isEditing, setIsEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [deleteOpened, setDeleteOpened] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState('');
  const { canWrite, isAdmin } = useMe();
  const [extracting, setExtracting] = useState(false);
  const [extractResult, setExtractResult] = useState<{ ok: boolean; text: string } | null>(null);

  // 케이스에서 재사용 지식을 뽑아 지식 베이스에 draft로 등록한다.
  // 케이스가 해결되기 전이라도 벤더 확답 같은 건 남길 가치가 있어 상태와 무관하게 동작한다.
  const extractKnowledge = async () => {
    setExtracting(true);
    setExtractResult(null);
    try {
      const res = await apiFetch(`/api/cases/${id}/knowledge/`, { method: 'POST' });
      const data = await res.json().catch(() => ({}));
      if (res.ok) {
        const k = data.item;
        setExtractResult({
          ok: true,
          text: data.outcome === 'exists'
            ? `이미 지식으로 등록된 케이스입니다 (${k.knowledge_id}).`
            : `${k.knowledge_id}로 등록했습니다 — "${k.title}"`,
        });
      } else {
        setExtractResult({ ok: false, text: data.error || `등록 실패 (HTTP ${res.status})` });
      }
    } catch {
      setExtractResult({ ok: false, text: '등록 실패: 백엔드 서버에 연결할 수 없습니다.' });
    } finally {
      setExtracting(false);
    }
  };

  const form = useForm({
    initialValues: {
      vendor: '',
      status: '',
      summary: '',
      description: '',
      action_steps: '',
      resolution: '',
    },
  });

  const loadCase = () => {
    apiFetch(`/api/cases/${id}/`)
      .then((res) => {
        if (res.ok) return res.json();
        throw new Error('Failed to fetch case');
      })
      .then((data) => {
          setCaseDetail(data);
          form.setValues({
              vendor: data.vendor,
              status: data.status,
              summary: data.summary,
              description: data.description || '',
              action_steps: data.action_steps || '',
              resolution: data.resolution || '',
          });
      })
      .catch((err) => console.error(err))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    if (id) loadCase();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  const [relationInput, setRelationInput] = useState('');
  const [relationError, setRelationError] = useState('');

  const addRelation = async () => {
    if (!relationInput.trim()) return;
    const response = await apiFetch(`/api/cases/${id}/relations/`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ case_id: relationInput }),
    });
    const data = await response.json().catch(() => null);
    if (response.ok) {
      setRelationInput('');
      setRelationError('');
      loadCase();
    } else {
      setRelationError(data?.error || '참조 추가에 실패했습니다.');
    }
  };

  const removeRelation = async (otherId: number) => {
    const response = await apiFetch(`/api/cases/${id}/relations/${otherId}/`, {
      method: 'DELETE',
    });
    if (response.ok) loadCase();
  };

  const handleSave = async () => {
      setSaving(true);
      try {
          const response = await apiFetch(`/api/cases/${id}/`, {
              method: 'PATCH',
              headers: {
                  'Content-Type': 'application/json',
              },
              body: JSON.stringify(form.values),
          });

          if (response.ok) {
              const updatedData = await response.json();
              setCaseDetail(updatedData);
              setIsEditing(false);
          } else {
              console.error("Failed to update case");
          }
      } catch (error) {
          console.error("Error updating case:", error);
      } finally {
          setSaving(false);
      }
  };

  // 삭제는 관리자만 (서버도 IsAdminRole로 차단). 케이스에 딸린 메일도 함께 지워진다.
  const handleDelete = async () => {
    setDeleting(true);
    setDeleteError('');
    try {
      const response = await apiFetch(`/api/cases/${id}/`, { method: 'DELETE' });
      if (response.ok) {
        router.push('/');
      } else {
        setDeleteError('삭제에 실패했습니다. 권한을 확인해 주세요.');
      }
    } catch {
      setDeleteError('삭제 요청 중 오류가 발생했습니다.');
    } finally {
      setDeleting(false);
    }
  };

  const handleCancel = () => {
      if (caseDetail) {
        form.setValues({
            vendor: caseDetail.vendor,
            status: caseDetail.status,
            summary: caseDetail.summary,
            description: caseDetail.description || '',
            action_steps: caseDetail.action_steps || '',
            resolution: caseDetail.resolution || '',
        });
      }
      setIsEditing(false);
  };

  if (loading) {
    return (
      <Center h="100vh">
        <Loader size="xl" />
      </Center>
    );
  }

  if (!caseDetail) {
    return (
      <Center h="100vh">
        <Text>Case not found</Text>
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
          <Group justify="space-between" mb="md">
             <Button
                variant="subtle"
                leftSection={<IconArrowLeft size={16} />}
                // 히스토리 back으로 돌아가야 목록의 필터·페이지 상태(URL 쿼리)가 복원된다.
                // 상세 URL로 직접 진입해 히스토리가 없으면 목록 첫 화면으로.
                onClick={() => (window.history.length > 1 ? router.back() : router.push('/'))}
            >
                Back to Cases
            </Button>
            
            {!isEditing ? (
                <Group>
                    {canWrite && (
                    <Button
                        variant="light"
                        color="grape"
                        leftSection={<IconBulb size={16} />}
                        loading={extracting}
                        onClick={extractKnowledge}
                    >
                        지식으로 저장
                    </Button>
                    )}
                    {canWrite && (
                    <Button
                        leftSection={<IconEdit size={16} />}
                        onClick={() => setIsEditing(true)}
                    >
                        Edit Case
                    </Button>
                    )}
                    {isAdmin && (
                    <Button
                        color="red"
                        variant="light"
                        leftSection={<IconTrash size={16} />}
                        onClick={() => { setDeleteError(''); setDeleteOpened(true); }}
                    >
                        삭제
                    </Button>
                    )}
                </Group>
            ) : (
                <Group>
                    <Button variant="default" onClick={handleCancel} disabled={saving}>Cancel</Button>
                    <Button 
                        leftSection={<IconDeviceFloppy size={16} />} 
                        onClick={handleSave} 
                        loading={saving}
                    >
                        Save Changes
                    </Button>
                </Group>
            )}
          </Group>

          {extractResult && (
            <Text size="sm" c={extractResult.ok ? 'teal' : 'red'} mb="sm">
              {extractResult.text}
              {extractResult.ok && (
                <Button
                  size="compact-xs"
                  variant="subtle"
                  ml="xs"
                  onClick={() => router.push('/knowledge')}
                >
                  지식 베이스에서 보기
                </Button>
              )}
            </Text>
          )}

          <Paper shadow="xs" p="xl" withBorder>
            <Group justify="space-between" mb="md">
              <Group>
                 <Title order={2}>{caseDetail.case_id}</Title>
                 {isEditing ? (
                     <Select 
                        data={['A10', 'Arista', 'HPE Aruba', 'Juniper']} 
                        {...form.getInputProps('vendor')}
                        w={150}
                     />
                 ) : (
                    <Badge size="lg" color={getVendorColor(caseDetail.vendor)}>{caseDetail.vendor}</Badge>
                 )}
              </Group>

              {isEditing ? (
                  <Select 
                    data={['Open', 'Resolved', 'Pending']} 
                    {...form.getInputProps('status')}
                    w={150}
                  />
              ) : (
                <Badge size="lg" color={getStatusColor(caseDetail.status)} variant="dot">{caseDetail.status}</Badge>
              )}
            </Group>

            {(caseDetail.device_model || caseDetail.device_serial || caseDetail.software_version) && (
              <Group gap="xs" mt="xs">
                {caseDetail.device_model && (
                  <Badge variant="outline" color="gray" radius="sm">
                    장비 {caseDetail.device_model}
                  </Badge>
                )}
                {caseDetail.software_version && (
                  <Badge variant="outline" color="gray" radius="sm">
                    SW {caseDetail.software_version}
                  </Badge>
                )}
                {caseDetail.device_serial && (
                  <Badge variant="outline" color="gray" radius="sm" style={{ textTransform: 'none' }}>
                    S/N {caseDetail.device_serial}
                  </Badge>
                )}
              </Group>
            )}

            {caseDetail.analyzed_by && (
              <Text size="xs" c="dimmed" mt="xs">
                AI 분석: {caseDetail.analyzed_by}
              </Text>
            )}

            <Divider my="sm" />

            <Stack gap="md" mt="md">
              <div>
                <Text fw={700} size="lg" mb="xs">Summary</Text>
                {isEditing ? (
                    <TextInput {...form.getInputProps('summary')} />
                ) : (
                    <Text>{caseDetail.summary}</Text>
                )}
              </div>

              <div>
                <Text fw={700} size="lg" mb="xs">Description</Text>
                {isEditing ? (
                    <Textarea minRows={3} {...form.getInputProps('description')} />
                ) : (
                    <Paper withBorder p="md" bg="gray.0">
                        <Text size="sm" style={bodyTextStyle}>
                            {caseDetail.description || <Text c="dimmed" fs="italic">No description provided</Text>}
                        </Text>
                    </Paper>
                )}
              </div>

               <div>
                <Text fw={700} size="lg" mb="xs">Action Taken</Text>
                 {isEditing ? (
                    <Textarea minRows={3} {...form.getInputProps('action_steps')} />
                ) : (
                    <Paper withBorder p="md" bg="gray.0">
                        <Text size="sm" style={bodyTextStyle}>
                            {caseDetail.action_steps || <Text c="dimmed" fs="italic">No actions recorded</Text>}
                        </Text>
                    </Paper>
                )}
              </div>

               <div>
                <Text fw={700} size="lg" mb="xs">Resolution</Text>
                 {isEditing ? (
                    <Textarea minRows={3} {...form.getInputProps('resolution')} />
                ) : (
                    <Paper withBorder p="md" bg="green.0">
                        <Text size="sm" style={bodyTextStyle}>
                            {caseDetail.resolution || <Text c="dimmed" fs="italic">No resolution recorded</Text>}
                        </Text>
                    </Paper>
                )}
              </div>

              <Text c="dimmed" size="sm" mt="xl">
                Created on: {new Date(caseDetail.created_at).toLocaleString()}
                {caseDetail.vendor_case_number && ` · Vendor Case #${caseDetail.vendor_case_number}`}
                {caseDetail.source === 'email' && ' · Gmail에서 자동 등록됨'}
              </Text>
            </Stack>
          </Paper>

          <Paper shadow="xs" p="xl" withBorder mt="lg">
            <Title order={3} mb="md">관련 케이스 ({caseDetail.related_cases?.length ?? 0})</Title>
            <Stack gap="xs">
              {(caseDetail.related_cases ?? []).map((rc) => (
                <Group key={rc.id} justify="space-between" wrap="nowrap">
                  <Group
                    gap="xs"
                    wrap="nowrap"
                    style={{ cursor: 'pointer', flex: 1, minWidth: 0 }}
                    onClick={() => { setLoading(true); router.push(`/cases/${rc.id}`); }}
                  >
                    <Text fw={600} style={{ whiteSpace: 'nowrap' }}>{rc.case_id}</Text>
                    <Badge color={getVendorColor(rc.vendor)} variant="light">{rc.vendor}</Badge>
                    <Badge color={getStatusColor(rc.status)} variant="dot">{rc.status}</Badge>
                    <Text size="sm" lineClamp={1} style={{ flex: 1 }}>
                      {rc.vendor_case_number && `[#${rc.vendor_case_number}] `}{rc.summary}
                    </Text>
                  </Group>
                  {canWrite && (
                    <Button size="xs" variant="subtle" color="red" onClick={() => removeRelation(rc.id)}>
                      해제
                    </Button>
                  )}
                </Group>
              ))}
              {(caseDetail.related_cases ?? []).length === 0 && (
                <Text c="dimmed" size="sm">연결된 케이스가 없습니다. 같은 사건의 별도 트랙 케이스를 참조로 연결하세요.</Text>
              )}
            </Stack>
            {canWrite && (
              <Group mt="md" gap="xs" align="flex-start">
                <TextInput
                  placeholder="예: C-1118"
                  value={relationInput}
                  onChange={(e) => setRelationInput(e.currentTarget.value)}
                  onKeyDown={(e) => e.key === 'Enter' && addRelation()}
                  size="xs"
                  w={140}
                  error={relationError || undefined}
                />
                <Button size="xs" variant="light" onClick={addRelation}>참조 추가</Button>
              </Group>
            )}
          </Paper>

          {caseDetail.emails && caseDetail.emails.length > 0 && (
            <Paper shadow="xs" p="xl" withBorder mt="lg">
              <Title order={3} mb="md">이메일 타임라인 ({caseDetail.emails.length})</Title>
              <Stack gap="md">
                {caseDetail.emails.map((email) => (
                  <EmailCard key={email.id} email={email} />
                ))}
              </Stack>
            </Paper>
          )}
        </Container>

        <Modal
          opened={deleteOpened}
          onClose={() => setDeleteOpened(false)}
          title={`케이스 삭제 — ${caseDetail.case_id}`}
          centered
        >
          <Stack>
            <Text size="sm">
              <Text span fw={600}>{caseDetail.summary}</Text> 케이스를 완전히 삭제합니다.
              이 작업은 되돌릴 수 없습니다.
            </Text>
            {caseDetail.emails?.length > 0 && (
              <Text size="sm" c="red">
                이메일 타임라인 {caseDetail.emails.length}통도 함께 삭제됩니다.
              </Text>
            )}
            <Text size="xs" c="dimmed">
              중복 생성된 케이스라면 삭제 대신 병합이 필요할 수 있습니다.
              이 케이스에서 추출된 지식 항목은 삭제되지 않고 남습니다.
            </Text>
            {deleteError && <Text size="sm" c="red">{deleteError}</Text>}
            <Group justify="flex-end">
              <Button variant="default" onClick={() => setDeleteOpened(false)} disabled={deleting}>
                취소
              </Button>
              <Button color="red" loading={deleting} onClick={handleDelete}>
                삭제
              </Button>
            </Group>
          </Stack>
        </Modal>

        {/* 이메일 타임라인이 길어 스크롤이 깊어지면 맨 위로 복귀 */}
        <ScrollToTopButton />
      </AppShell.Main>
    </AppShell>
  );
}

// HTML 메일을 텍스트로 변환하며 생긴 과도한 빈 줄(3개 이상 연속)을 문단 구분
// 1개로 줄인다 — 이미 저장된 메일 본문도 화면에서는 정리해 보여주기 위함
function normalizeEmailBody(text: string) {
  return text.replace(/[ \t]+\n/g, '\n').replace(/\n{3,}/g, '\n\n').trim();
}

function EmailCard({ email }: { email: CaseEmail }) {
  const [showOriginal, setShowOriginal] = useState(false);
  const inbound = email.direction === 'inbound';
  const hasTranslation = !!email.body_ko;
  // 번역이 없으면 원문만 표시
  const body = normalizeEmailBody(
    showOriginal || !hasTranslation ? email.body_original : email.body_ko
  );
  const subject = showOriginal || !email.subject_ko ? email.subject : email.subject_ko;

  return (
    <Paper withBorder p="md" bg={inbound ? 'blue.0' : 'gray.0'}>
      <Group justify="space-between" mb="xs">
        <Group gap="xs">
          <Badge
            color={inbound ? 'blue' : 'gray'}
            variant="light"
            leftSection={inbound ? <IconMailDown size={12} /> : <IconMailUp size={12} />}
          >
            {inbound ? '수신 (벤더)' : '발신'}
          </Badge>
          <Text size="sm" c="dimmed">{email.sender}</Text>
        </Group>
        <Group gap="xs">
          <Text size="sm" c="dimmed">{new Date(email.received_at).toLocaleString()}</Text>
          {hasTranslation && (
            <Button
              size="compact-xs"
              variant={showOriginal ? 'filled' : 'default'}
              leftSection={<IconLanguage size={12} />}
              onClick={() => setShowOriginal((v) => !v)}
            >
              {showOriginal ? '번역 보기' : '원문 보기'}
            </Button>
          )}
        </Group>
      </Group>
      <Text fw={600} mb="xs">{subject}</Text>
      <Text size="sm" style={bodyTextStyle}>{body}</Text>
      {!hasTranslation && (
        <Text size="xs" c="dimmed" fs="italic" mt="xs">
          번역본이 없습니다 (원문 표시 중)
        </Text>
      )}
    </Paper>
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
