'use client';

import { useEffect, useRef, useState } from 'react';
import {
  ActionIcon,
  Box,
  Drawer,
  Group,
  Loader,
  Paper,
  ScrollArea,
  Stack,
  Text,
  Textarea,
} from '@mantine/core';
import { IconSend, IconSparkles } from '@tabler/icons-react';
import { apiFetch } from '../lib/api';

interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
  toolNote?: string; // 답변 근거로 사용한 도구 호출 표시
}

const TOOL_LABELS: Record<string, string> = {
  search_cases: '케이스 검색',
  get_case_detail: '상세 조회',
  get_case_stats: '통계 집계',
};

const WELCOME =
  '케이스 이력 도우미입니다. 예: "VRRP failover 유사 사례 찾아줘", ' +
  '"C-1122 지금 상태 어때?", "이번 달 A10 케이스 몇 건이야?"';

export default function HelpAgentDrawer({
  opened,
  onClose,
}: {
  opened: boolean;
  onClose: () => void;
}) {
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

  const send = async () => {
    const question = input.trim();
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
        { role: 'assistant', content: data.reply, toolNote },
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
    <Drawer
      opened={opened}
      onClose={onClose}
      position="right"
      size="md"
      title={
        <Group gap={8}>
          <IconSparkles size={18} color="var(--mantine-color-blue-6)" />
          <Text fw={600}>케이스 도우미</Text>
        </Group>
      }
    >
      <Stack h="calc(100vh - 80px)" gap="sm">
        <ScrollArea style={{ flex: 1 }} viewportRef={viewportRef}>
          <Stack gap="sm" pb="sm">
            <Paper p="sm" radius="md" bg="var(--mantine-color-gray-0)">
              <Text size="sm" c="dimmed">{WELCOME}</Text>
            </Paper>
            {messages.map((m, i) => (
              <Box
                key={i}
                style={{
                  alignSelf: m.role === 'user' ? 'flex-end' : 'flex-start',
                  maxWidth: '90%',
                }}
              >
                <Paper
                  p="sm"
                  radius="md"
                  bg={
                    m.role === 'user'
                      ? 'var(--mantine-color-blue-0)'
                      : 'var(--mantine-color-gray-0)'
                  }
                >
                  <Text size="sm" style={{ whiteSpace: 'pre-wrap' }}>
                    {m.content}
                  </Text>
                </Paper>
                {m.toolNote && (
                  <Text size="xs" c="dimmed" mt={2} ml={4}>
                    DB 조회: {m.toolNote}
                  </Text>
                )}
              </Box>
            ))}
            {loading && (
              <Group gap={8} ml={4}>
                <Loader size="xs" />
                <Text size="xs" c="dimmed">케이스 DB를 확인하는 중...</Text>
              </Group>
            )}
          </Stack>
        </ScrollArea>

        <Group gap="xs" align="flex-end">
          <Textarea
            style={{ flex: 1 }}
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
            variant="filled"
            onClick={send}
            disabled={!input.trim() || loading}
            aria-label="질문 보내기"
          >
            <IconSend size={18} />
          </ActionIcon>
        </Group>
      </Stack>
    </Drawer>
  );
}
