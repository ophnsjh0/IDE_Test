'use client';

import { useEffect, useRef, useState } from 'react';
import {
  ActionIcon,
  Affix,
  Box,
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
import { IconRobotFace, IconSend, IconDatabase } from '@tabler/icons-react';
import { apiFetch } from '../lib/api';
import classes from './HelpAgentWidget.module.css';

interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
  toolNote?: string; // 답변 근거로 사용한 도구 호출 표시
  agent?: string; // 트리아지가 배정한 담당 에이전트 (search | report)
}

const AGENT_LABELS: Record<string, string> = {
  search: '검색',
  report: '리포팅',
  tech: '기술지원',
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
  const viewportRef = useRef<HTMLDivElement>(null);

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
        { role: 'assistant', content: data.reply, toolNote, agent: data.agent },
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
        size="md"
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
                    <Text size="sm" style={{ whiteSpace: 'pre-wrap', color: 'inherit' }}>
                      {m.content}
                    </Text>
                  </div>
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
                  <Text size="xs" c="dimmed">케이스 DB를 확인하는 중...</Text>
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
