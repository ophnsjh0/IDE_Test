'use client';

import { useEffect, useRef, useState } from 'react';
import {
  ActionIcon,
  Affix,
  Box,
  Button,
  Drawer,
  Group,
  Loader,
  Paper,
  ScrollArea,
  Stack,
  Text,
  Textarea,
  ThemeIcon,
  Tooltip,
} from '@mantine/core';
import { IconRobotFace, IconSend, IconDatabase, IconFileDownload } from '@tabler/icons-react';
import { apiFetch } from '../lib/api';
import classes from './HelpAgentWidget.module.css';

interface GeneratedFile {
  file_id: string;
  filename: string;
  size_bytes: number;
}

interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
  toolNote?: string; // 답변 근거로 사용한 도구 호출 표시
  agent?: string; // 트리아지가 배정한 담당 에이전트 (search | report)
  files?: GeneratedFile[]; // 리포팅 에이전트가 생성한 문서 (워드/엑셀/PPT)
}

const AGENT_LABELS: Record<string, string> = {
  search: '검색',
  report: '리포팅',
  tech: '기술지원',
  off_topic: '안내',
};

const TOOL_LABELS: Record<string, string> = {
  search_cases: '케이스 검색',
  get_case_detail: '상세 조회',
  get_case_stats: '통계 집계',
  list_recent_cases: '최근 케이스',
  web_search: '웹 검색',
};

const SUGGESTIONS = [
  'VRRP failover 유사 사례 찾아줘',
  '최근 30일 케이스 리포트 작성해줘',
  'ACOS 6.0.8 알려진 버그 검색해줘',
];

// Drawer 기본 너비(px) — Mantine size="md"와 동일. 드래그 시 이 값 미만으로는 줄지 않는다.
const DRAWER_DEFAULT_WIDTH = 440;

// AI 도우미 런처 + 채팅 Drawer를 묶은 위젯.
// variant='inline'  : 페이지 레이아웃 안에 일반 버튼으로 배치 (리스트 페이지)
// variant='floating': 화면 우측 하단 고정 네모 버튼 (그 외 페이지)
export default function HelpAgentWidget({
  variant = 'floating',
}: {
  variant?: 'inline' | 'floating';
}) {
  const [opened, setOpened] = useState(false);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [drawerWidth, setDrawerWidth] = useState(DRAWER_DEFAULT_WIDTH);
  const viewportRef = useRef<HTMLDivElement>(null);

  // 좌측 가장자리 드래그로 너비 조절 — 표 등 긴 내용을 볼 때 넓혀 쓴다
  const startResize = (e: React.PointerEvent) => {
    e.preventDefault();
    const startX = e.clientX;
    const startWidth = drawerWidth;
    const maxWidth = Math.round(window.innerWidth * 0.9);
    const onMove = (ev: PointerEvent) => {
      const next = startWidth + (startX - ev.clientX);
      setDrawerWidth(Math.min(Math.max(next, DRAWER_DEFAULT_WIDTH), maxWidth));
    };
    const onUp = () => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    };
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
  };

  useEffect(() => {
    viewportRef.current?.scrollTo({
      top: viewportRef.current.scrollHeight,
      behavior: 'smooth',
    });
  }, [messages, loading]);

  const send = async (text?: string) => {
    const question = (text ?? input).trim();
    if (!question || loading) return;
    const history = [...messages, { role: 'user' as const, content: question }];
    setMessages(history);
    setInput('');
    setLoading(true);
    try {
      const res = await apiFetch('/api/help-agent/chat/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          messages: history.map(({ role, content }) => ({ role, content })),
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
      const toolNote = (data.tool_calls || [])
        .map((t: { name: string }) => TOOL_LABELS[t.name] || t.name)
        .join(' → ');
      setMessages((prev) => [
        ...prev,
        {
          role: 'assistant',
          content: data.reply,
          toolNote,
          agent: data.agent,
          files: data.files,
        },
      ]);
    } catch (e) {
      setMessages((prev) => [
        ...prev,
        {
          role: 'assistant',
          content: `오류가 발생했습니다: ${e instanceof Error ? e.message : e}`,
        },
      ]);
    } finally {
      setLoading(false);
    }
  };

  // 생성 문서 다운로드 — 세션 쿠키 인증이 필요해 apiFetch(blob)로 받는다
  const downloadFile = async (file: GeneratedFile) => {
    try {
      const res = await apiFetch(`/api/help-agent/files/${file.file_id}/`);
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.error || `HTTP ${res.status}`);
      }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = file.filename;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      setMessages((prev) => [
        ...prev,
        {
          role: 'assistant',
          content: `파일 다운로드에 실패했습니다: ${e instanceof Error ? e.message : e}`,
        },
      ]);
    }
  };

  return (
    <>
      {variant === 'inline' ? (
        <button
          type="button"
          className={classes.launcherInline}
          onClick={() => setOpened(true)}
        >
          <IconRobotFace size={18} />
          AI 도우미
        </button>
      ) : (
        !opened && (
          <Affix position={{ bottom: 120, right: 24 }}>
            <Tooltip label="AI 도우미에게 케이스 질문" position="left">
              <button
                type="button"
                className={classes.launcher}
                aria-label="AI 도우미"
                onClick={() => setOpened(true)}
              >
                <IconRobotFace size={26} />
                <span className={classes.launcherLabel}>AI</span>
              </button>
            </Tooltip>
          </Affix>
        )
      )}

      <Drawer
        opened={opened}
        onClose={() => setOpened(false)}
        position="right"
        size={drawerWidth}
        styles={{ content: { position: 'relative' } }}
        title={
          <Group gap={10}>
            <ThemeIcon
              size={34}
              radius="md"
              variant="gradient"
              gradient={{ from: 'blue', to: 'violet', deg: 135 }}
            >
              <IconRobotFace size={20} />
            </ThemeIcon>
            <div>
              <Text fw={700} size="sm" lh={1.2}>AI 도우미</Text>
              <Text size="xs" c="dimmed" lh={1.2}>케이스 이력 검색 · DB 근거 답변</Text>
            </div>
          </Group>
        }
      >
        {/* 좌측 가장자리를 드래그하면 창이 넓어진다 (더블클릭: 기본 크기로 복원) */}
        <div
          className={classes.resizeHandle}
          onPointerDown={startResize}
          onDoubleClick={() => setDrawerWidth(DRAWER_DEFAULT_WIDTH)}
          title="드래그로 창 너비 조절 · 더블클릭으로 기본 크기"
        />

        {/* 입력란이 화면 맨 아래에 붙지 않도록 높이를 줄여 위쪽에 배치 */}
        <Stack h="calc(100vh - 180px)" gap="sm">
          <ScrollArea style={{ flex: 1 }} viewportRef={viewportRef}>
            <Stack gap="md" pb="sm">
              <Paper
                p="md"
                radius="lg"
                style={{
                  background:
                    'linear-gradient(135deg, var(--mantine-color-blue-0), var(--mantine-color-violet-0))',
                }}
              >
                <Text size="sm" fw={600} mb={4}>무엇을 도와드릴까요?</Text>
                <Text size="xs" c="dimmed" mb="sm">
                  케이스 이력·유사 사례·현황을 DB에서 찾아 근거와 함께 답해드려요.
                </Text>
                <Group gap={6}>
                  {SUGGESTIONS.map((s) => (
                    <button
                      key={s}
                      type="button"
                      className={classes.suggestion}
                      onClick={() => send(s)}
                    >
                      {s}
                    </button>
                  ))}
                </Group>
              </Paper>

              {messages.map((m, i) => (
                <Box
                  key={i}
                  style={{
                    alignSelf: m.role === 'user' ? 'flex-end' : 'flex-start',
                    maxWidth: '88%',
                  }}
                >
                  {m.role === 'assistant' && (
                    <Group gap={6} mb={4}>
                      <ThemeIcon
                        size={20}
                        radius="xl"
                        variant="gradient"
                        gradient={{ from: 'blue', to: 'violet', deg: 135 }}
                      >
                        <IconRobotFace size={12} />
                      </ThemeIcon>
                      <Text size="xs" c="dimmed" fw={600}>
                        AI 도우미
                        {m.agent && AGENT_LABELS[m.agent]
                          ? ` · ${AGENT_LABELS[m.agent]}`
                          : ''}
                      </Text>
                    </Group>
                  )}
                  <div className={m.role === 'user' ? classes.bubbleUser : classes.bubbleAssistant}>
                    <Text
                      size="sm"
                      style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', color: 'inherit' }}
                    >
                      {m.content}
                    </Text>
                  </div>
                  {m.files && m.files.length > 0 && (
                    <Stack gap={6} mt={8}>
                      {m.files.map((f) => (
                        <Button
                          key={f.file_id}
                          size="xs"
                          variant="light"
                          leftSection={<IconFileDownload size={14} />}
                          onClick={() => downloadFile(f)}
                          styles={{ inner: { justifyContent: 'flex-start' } }}
                        >
                          {f.filename} ({Math.max(1, Math.round(f.size_bytes / 1024))} KB)
                        </Button>
                      ))}
                    </Stack>
                  )}
                  {m.toolNote && (
                    <Group gap={4} mt={4} ml={4}>
                      <IconDatabase size={12} color="var(--mantine-color-gray-5)" />
                      <Text size="xs" c="dimmed">{m.toolNote}</Text>
                    </Group>
                  )}
                </Box>
              ))}

              {loading && (
                <Group gap={8} ml={4}>
                  <ThemeIcon
                    size={20}
                    radius="xl"
                    variant="gradient"
                    gradient={{ from: 'blue', to: 'violet', deg: 135 }}
                  >
                    <IconRobotFace size={12} />
                  </ThemeIcon>
                  <Loader size="xs" type="dots" />
                  <Text size="xs" c="dimmed">
                    답변을 준비하는 중... (리포트 문서 생성은 1~2분 걸릴 수 있어요)
                  </Text>
                </Group>
              )}
            </Stack>
          </ScrollArea>

          <div className={classes.inputWrap}>
            <Group gap="xs" align="flex-end">
              <Textarea
                style={{ flex: 1 }}
                variant="unstyled"
                placeholder="케이스에 대해 물어보세요"
                autosize
                minRows={1}
                maxRows={4}
                value={input}
                onChange={(e) => setInput(e.currentTarget.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
                    e.preventDefault();
                    send();
                  }
                }}
              />
              <ActionIcon
                size="lg"
                radius="md"
                variant="gradient"
                gradient={{ from: 'blue', to: 'violet', deg: 135 }}
                onClick={() => send()}
                disabled={!input.trim() || loading}
                aria-label="질문 보내기"
              >
                <IconSend size={18} />
              </ActionIcon>
            </Group>
          </div>
        </Stack>
      </Drawer>
    </>
  );
}
