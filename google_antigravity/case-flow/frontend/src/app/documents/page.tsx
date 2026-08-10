'use client';

import { useEffect, useState } from 'react';
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
  Switch,
  Modal,
  FileInput,
  Checkbox,
  Stack,
  Tooltip,
  ActionIcon,
} from '@mantine/core';
import {
  IconSearch,
  IconRefresh,
  IconDownload,
  IconExternalLink,
  IconUpload,
  IconTrash,
  IconDatabaseImport,
  IconFileTypePdf,
  IconFileTypeXls,
} from '@tabler/icons-react';
import AppHeader from '../components/AppHeader';
import ScrollToTopButton from '../components/ScrollToTopButton';
import { apiFetch, apiUrl } from '../lib/api';
import { useMe } from '../lib/useMe';

// 서버가 JSON이 아닌 응답(예: Django HTML 500 페이지)을 줄 때도 원인을 잃지 않도록
// 방어적으로 파싱한다. res.ok 확인 전에 res.json()을 부르면 파싱 예외가 catch로
// 빠져 실제 원인이 "서버 연결 실패"로 둔갑한다 (2026-08-10 실장애).
async function readError(res: Response): Promise<string> {
  const data = await res.json().catch(() => null);
  return data?.error || `서버 오류 (HTTP ${res.status})`;
}

interface DocumentItem {
  filename: string; // "A10/config/xxx.pdf" — API path 파라미터로 그대로 사용
  name: string;
  vendor: string;
  doc_type: string;
  size: number;
  modified_at: string;
  title: string;
  page_count: number;
  chunk_count: number;
  embedded: boolean;
  embedded_at: string | null;
}

interface DocumentList {
  items: DocumentItem[];
  pending: number;
  auto_embed: boolean;
  embedding_model: string;
  embedding_key_configured: boolean;
}

const VENDOR_TABS = [
  { value: 'all', label: 'All Vendors', color: 'blue' },
  { value: 'A10', label: 'A10', color: 'orange' },
  { value: 'Arista', label: 'Arista', color: 'blue' },
  { value: 'HPE Aruba', label: 'HPE Aruba', color: 'green' },
  { value: 'Juniper', label: 'Juniper', color: 'violet' },
];

const DOC_TYPE_LABELS: Record<string, string> = {
  config: '설정 가이드',
  release: '릴리스 노트',
  issues: '알려진 이슈',
};

function formatSize(bytes: number) {
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  if (bytes >= 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${bytes} B`;
}

export default function DocumentsPage() {
  const [data, setData] = useState<DocumentList | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [activeTab, setActiveTab] = useState<string | null>('all');
  const [searchQuery, setSearchQuery] = useState('');
  const [message, setMessage] = useState('');
  const [embedding, setEmbedding] = useState(false);
  const [embeddingFile, setEmbeddingFile] = useState('');
  const [autoSaving, setAutoSaving] = useState(false);
  // 업로드 모달
  const [uploadOpen, setUploadOpen] = useState(false);
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [uploadVendor, setUploadVendor] = useState<string | null>(null);
  const [uploadDocType, setUploadDocType] = useState<string | null>('config');
  const [uploadOverwrite, setUploadOverwrite] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState('');
  const { canWrite, isAdmin } = useMe();

  const fetchDocuments = async () => {
    setLoading(true);
    setLoadError('');
    try {
      const response = await apiFetch('/api/references/');
      if (response.ok) {
        setData(await response.json());
      } else {
        setLoadError(`문서 목록을 불러오지 못했습니다 (HTTP ${response.status}).`);
      }
    } catch (error) {
      console.error('Error fetching documents:', error);
      setLoadError('백엔드 서버(:8000)에 연결할 수 없습니다. 서버 실행 상태를 확인하세요.');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchDocuments();
  }, []);

  const toggleAutoEmbed = async (enabled: boolean) => {
    setAutoSaving(true);
    setMessage('');
    try {
      const response = await apiFetch('/api/settings/reference-auto-embed/', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled }),
      });
      if (response.ok) {
        const result = await response.json();
        setData((prev) => (prev ? { ...prev, auto_embed: result.enabled } : prev));
        setMessage(`업로드 자동 임베딩을 ${result.enabled ? '켰습니다' : '껐습니다'}.`);
      } else {
        setMessage(`설정 변경 실패: ${await readError(response)}`);
      }
    } catch {
      setMessage('설정 변경 실패: 백엔드 서버에 연결할 수 없습니다.');
    } finally {
      setAutoSaving(false);
    }
  };

  // path 없이 호출하면 신규/변경 문서 전체, path를 주면 그 문서만 강제 재임베딩
  const runEmbed = async (path?: string) => {
    setEmbedding(true);
    setEmbeddingFile(path || '');
    setMessage(path ? `${path} 임베딩 중...` : '미임베딩 문서 처리 중... (문서 크기에 따라 수십 초 걸릴 수 있습니다)');
    try {
      const response = await apiFetch('/api/references/embed/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(path ? { path } : {}),
      });
      if (response.ok) {
        const result = await response.json();
        setMessage(
          path
            ? `임베딩 완료: ${path}`
            : `임베딩 완료 — 신규 ${result.created}, 갱신 ${result.updated}, 변경 없음 ${result.skipped}` +
              (result.failed > 0 ? `, 실패 ${result.failed}건 (서버 로그 확인)` : '') +
              (result.removed > 0 ? `, 삭제 정리 ${result.removed}` : '')
        );
        fetchDocuments();
      } else {
        setMessage(`임베딩 실패: ${await readError(response)}`);
        fetchDocuments();
      }
    } catch {
      setMessage('임베딩 실패: 백엔드 서버에 연결할 수 없습니다.');
    } finally {
      setEmbedding(false);
      setEmbeddingFile('');
    }
  };

  const handleUpload = async () => {
    if (!uploadFile || !uploadVendor) {
      setUploadError('파일과 벤더를 선택하세요.');
      return;
    }
    setUploading(true);
    setUploadError('');
    const form = new FormData();
    form.append('file', uploadFile);
    form.append('vendor', uploadVendor);
    form.append('doc_type', uploadDocType || '');
    if (uploadOverwrite) form.append('overwrite', 'true');
    try {
      const response = await apiFetch('/api/references/upload/', {
        method: 'POST',
        body: form,
      });
      if (response.ok) {
        const result = await response.json();
        setUploadOpen(false);
        setUploadFile(null);
        setUploadOverwrite(false);
        setMessage(
          result.embedded
            ? `업로드 및 임베딩 완료: ${result.filename}`
            : `업로드 완료: ${result.filename}` +
              (result.embed_error
                ? ` — 임베딩 실패 (${result.embed_error})`
                : ' — 임베딩 대기 중 (관리자가 수동 임베딩으로 처리)')
        );
        fetchDocuments();
      } else {
        setUploadError(await readError(response));
      }
    } catch {
      setUploadError('업로드 실패: 백엔드 서버에 연결할 수 없습니다.');
    } finally {
      setUploading(false);
    }
  };

  const handleDelete = async (doc: DocumentItem) => {
    if (!window.confirm(`"${doc.name}" 문서를 삭제할까요?\n원본 파일과 임베딩 데이터가 함께 삭제되며 되돌릴 수 없습니다.`)) return;
    try {
      const response = await apiFetch(
        `/api/references/file/?path=${encodeURIComponent(doc.filename)}`,
        { method: 'DELETE' }
      );
      if (response.ok) {
        setMessage((await response.json()).message);
        fetchDocuments();
      } else {
        setMessage(`삭제 실패: ${await readError(response)}`);
      }
    } catch {
      setMessage('삭제 실패: 백엔드 서버에 연결할 수 없습니다.');
    }
  };

  const fileHref = (doc: DocumentItem, download: boolean) =>
    apiUrl(`/api/references/file/?path=${encodeURIComponent(doc.filename)}${download ? '&dl=1' : ''}`);

  const items = data?.items ?? [];
  const filtered = items.filter((doc) => {
    const vendorMatch = activeTab === 'all' || doc.vendor === activeTab;
    const q = searchQuery.toLowerCase();
    const searchMatch = [doc.name, doc.title, doc.filename, doc.doc_type]
      .some((field) => (field || '').toLowerCase().includes(q));
    return vendorMatch && searchMatch;
  });

  const rows = filtered.map((doc) => (
    <Table.Tr key={doc.filename}>
      <Table.Td>
        <Group gap="xs" wrap="nowrap">
          {doc.filename.toLowerCase().endsWith('.xlsx')
            ? <IconFileTypeXls size={20} color="var(--mantine-color-green-7)" style={{ flexShrink: 0 }} />
            : <IconFileTypePdf size={20} color="var(--mantine-color-red-7)" style={{ flexShrink: 0 }} />}
          <div style={{ minWidth: 0 }}>
            <Text size="sm" fw={600} style={{ wordBreak: 'break-all' }}>{doc.name}</Text>
            {doc.title && (
              <Text size="xs" c="dimmed" lineClamp={1}>{doc.title}</Text>
            )}
          </div>
        </Group>
      </Table.Td>
      <Table.Td style={{ whiteSpace: 'nowrap' }}>
        <Badge color={getVendorColor(doc.vendor)} variant="light">{doc.vendor}</Badge>
      </Table.Td>
      <Table.Td style={{ whiteSpace: 'nowrap' }}>
        {doc.doc_type ? (
          <Badge variant="outline" color="gray">
            {DOC_TYPE_LABELS[doc.doc_type] || doc.doc_type}
          </Badge>
        ) : (
          <Text size="sm" c="dimmed">—</Text>
        )}
      </Table.Td>
      <Table.Td style={{ whiteSpace: 'nowrap' }}>
        <Text size="sm">{formatSize(doc.size)}</Text>
        {doc.page_count > 0 && (
          <Text size="xs" c="dimmed">
            {doc.filename.toLowerCase().endsWith('.xlsx') ? `${doc.page_count}행` : `${doc.page_count}쪽`}
          </Text>
        )}
      </Table.Td>
      <Table.Td style={{ whiteSpace: 'nowrap' }}>
        {doc.embedded ? (
          <Tooltip label={`청크 ${doc.chunk_count}개 · ${doc.embedded_at?.slice(0, 10)}`}>
            <Badge color="green" variant="dot">임베딩됨</Badge>
          </Tooltip>
        ) : (
          <Badge color="yellow" variant="dot">미임베딩</Badge>
        )}
      </Table.Td>
      <Table.Td style={{ whiteSpace: 'nowrap' }}>
        <Text size="sm">{doc.modified_at.slice(0, 10)}</Text>
      </Table.Td>
      <Table.Td style={{ whiteSpace: 'nowrap' }}>
        <Group gap={4} wrap="nowrap" justify="flex-end">
          {/* PDF는 브라우저에서 바로 열어 출처 페이지를 확인할 수 있게 inline 보기 제공 */}
          {doc.filename.toLowerCase().endsWith('.pdf') && (
            <Tooltip label="브라우저에서 열기">
              <ActionIcon
                component="a"
                href={fileHref(doc, false)}
                target="_blank"
                variant="subtle"
              >
                <IconExternalLink size={16} />
              </ActionIcon>
            </Tooltip>
          )}
          <Tooltip label="원본 다운로드">
            <ActionIcon component="a" href={fileHref(doc, true)} variant="subtle">
              <IconDownload size={16} />
            </ActionIcon>
          </Tooltip>
          {isAdmin && (
            <>
              <Tooltip label={doc.embedded ? '강제 재임베딩' : '이 문서 임베딩'}>
                <ActionIcon
                  variant="subtle"
                  color="grape"
                  loading={embedding && embeddingFile === doc.filename}
                  disabled={embedding && embeddingFile !== doc.filename}
                  onClick={() => runEmbed(doc.filename)}
                >
                  <IconDatabaseImport size={16} />
                </ActionIcon>
              </Tooltip>
              <Tooltip label="문서 삭제">
                <ActionIcon variant="subtle" color="red" onClick={() => handleDelete(doc)}>
                  <IconTrash size={16} />
                </ActionIcon>
              </Tooltip>
            </>
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
        <Container size="xl">
          <Group justify="space-between" mb="lg">
            <div>
              <Title order={2}>Documents</Title>
              <Text c="dimmed">
                벤더 공식 문서 원본 — AI 답변·지식에 인용된 출처를 직접 확인하고 다운로드할 수 있습니다
              </Text>
            </div>
            <Group gap="xs">
              {isAdmin && data && (
                <Switch
                  label="업로드 자동 임베딩"
                  checked={data.auto_embed}
                  disabled={autoSaving}
                  onChange={(e) => toggleAutoEmbed(e.currentTarget.checked)}
                />
              )}
              {isAdmin && (
                <Button
                  leftSection={<IconDatabaseImport size={14} />}
                  variant="light"
                  color="grape"
                  loading={embedding && !embeddingFile}
                  disabled={embedding && !!embeddingFile}
                  onClick={() => runEmbed()}
                >
                  미임베딩 문서 임베딩{data && data.pending > 0 ? ` (${data.pending})` : ''}
                </Button>
              )}
              {canWrite && (
                <Button
                  leftSection={<IconUpload size={14} />}
                  onClick={() => { setUploadError(''); setUploadOpen(true); }}
                >
                  문서 업로드
                </Button>
              )}
              <Button leftSection={<IconRefresh size={14} />} variant="default" onClick={fetchDocuments}>
                Refresh
              </Button>
            </Group>
          </Group>

          {message && (
            <Text size="sm" c={/실패|오류/.test(message) ? 'red' : 'teal'} mb="sm">
              {message}
            </Text>
          )}
          {isAdmin && data && !data.embedding_key_configured && (
            <Text size="sm" c="orange" mb="sm">
              OPENAI_API_KEY가 설정되어 있지 않아 임베딩을 실행할 수 없습니다 (.env 확인).
            </Text>
          )}

          <Paper shadow="xs" p="md" withBorder>
            <Tabs
              value={activeTab}
              onChange={setActiveTab}
              mb="md"
              color={activeTab && activeTab !== 'all' ? getVendorColor(activeTab) : 'blue'}
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
                {VENDOR_TABS.map((t) => {
                  const active = activeTab === t.value;
                  const count = t.value === 'all'
                    ? items.length
                    : items.filter((d) => d.vendor === t.value).length;
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

            <Group mb="md">
              <TextInput
                placeholder="Search documents... (파일명, 문서 제목, 유형)"
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
                      <Table.Th>문서</Table.Th>
                      <Table.Th style={{ width: 110 }}>Vendor</Table.Th>
                      <Table.Th style={{ width: 120 }}>유형</Table.Th>
                      <Table.Th style={{ width: 100 }}>크기</Table.Th>
                      <Table.Th style={{ width: 110 }}>임베딩</Table.Th>
                      <Table.Th style={{ width: 110 }}>수정일</Table.Th>
                      <Table.Th style={{ width: 150, textAlign: 'right' }}>동작</Table.Th>
                    </Table.Tr>
                  </Table.Thead>
                  <Table.Tbody>{rows}</Table.Tbody>
                </Table>

                {rows.length === 0 && (
                  <Text c="dimmed" ta="center" py="xl">
                    {items.length === 0
                      ? '등록된 문서가 없습니다. 문서 업로드 버튼으로 벤더 공식 문서(PDF/XLSX)를 추가하세요.'
                      : 'No documents found'}
                  </Text>
                )}
              </>
            )}
          </Paper>
        </Container>

        <Modal
          opened={uploadOpen}
          onClose={() => !uploading && setUploadOpen(false)}
          title="문서 업로드"
          centered
        >
          <Stack gap="md">
            <FileInput
              label="파일 (PDF 또는 XLSX)"
              placeholder="파일 선택"
              accept=".pdf,.xlsx"
              value={uploadFile}
              onChange={setUploadFile}
              leftSection={<IconUpload size={14} />}
              required
            />
            <Select
              label="벤더"
              placeholder="벤더 선택"
              data={VENDOR_TABS.filter((t) => t.value !== 'all').map((t) => t.value)}
              value={uploadVendor}
              onChange={setUploadVendor}
              required
              allowDeselect={false}
            />
            <Select
              label="문서 유형"
              description="벤더 폴더 아래 저장 위치 — AI 검색 시 유형 필터로도 사용됩니다"
              data={[
                { value: 'config', label: '설정 가이드 (config)' },
                { value: 'release', label: '릴리스 노트 (release)' },
                { value: 'issues', label: '알려진 이슈 (issues)' },
                { value: '', label: '기타 (벤더 폴더 바로 아래)' },
              ]}
              value={uploadDocType}
              onChange={setUploadDocType}
              allowDeselect={false}
            />
            <Checkbox
              label="같은 이름의 문서가 있으면 덮어쓰기"
              checked={uploadOverwrite}
              onChange={(e) => setUploadOverwrite(e.currentTarget.checked)}
            />
            {data?.auto_embed === false && (
              <Text size="xs" c="dimmed">
                자동 임베딩이 꺼져 있어 업로드 후 관리자가 수동 임베딩해야 AI 검색에 반영됩니다.
              </Text>
            )}
            {uploadError && <Text size="sm" c="red">{uploadError}</Text>}
            <Group justify="flex-end">
              <Button variant="default" onClick={() => setUploadOpen(false)} disabled={uploading}>
                취소
              </Button>
              <Button onClick={handleUpload} loading={uploading}>
                업로드
              </Button>
            </Group>
          </Stack>
        </Modal>

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
