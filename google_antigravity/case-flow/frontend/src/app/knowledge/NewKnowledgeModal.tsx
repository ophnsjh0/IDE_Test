'use client';

import { useState } from 'react';
import {
  Button, Group, Modal, Select, Stack, Text, Textarea, TextInput,
} from '@mantine/core';
import { useForm } from '@mantine/form';
import { apiFetch } from '../lib/api';
import { SECTIONS } from './sections';

// 직접 작성이 필요한 자리: 랩에서 재현이 실패했을 때. 실패도 값진 기록이지만
// 왜 안 됐는지는 실행 기록에 남지 않고 돌려본 사람 머릿속에 있어서, AI 추출
// 경로로는 담을 수 없다.
//
// 칸은 추출된 지식과 똑같은 8칸을 쓴다(sections.ts). 사람이 쓴 것만 모양이
// 다르면 나중에 검색·비교가 어긋난다.
const VENDORS = ['A10', 'Arista', 'HPE Aruba', 'Juniper'];

// 이 두 칸이 없으면 지식이 아니라 메모다 — 서버도 같은 기준으로 막는다
const REQUIRED: string[] = ['problem', 'resolution'];

interface Props {
  opened: boolean;
  onClose: () => void;
  onCreated: (id: number) => void;
}

export default function NewKnowledgeModal({ opened, onClose, onCreated }: Props) {
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  const form = useForm({
    initialValues: {
      vendor: '',
      title: '',
      device_model: '',
      software_version: '',
      ...Object.fromEntries(SECTIONS.map((s) => [s.key, ''])),
    } as Record<string, string>,
    validate: {
      vendor: (v) => (v ? null : '벤더를 선택하세요.'),
      title: (v) => (v.trim() ? null : '제목이 필요합니다.'),
      problem: (v) => (v.trim() ? null : '문제 상황은 비울 수 없습니다.'),
      resolution: (v) => (v.trim() ? null : '해결 조치는 비울 수 없습니다.'),
    },
  });

  const submit = form.onSubmit(async (values) => {
    setSaving(true);
    setError('');
    try {
      const res = await apiFetch('/api/knowledge/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(values),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || Object.values(data).flat().join(' ')
        || `HTTP ${res.status}`);
      form.reset();
      onCreated(data.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  });

  return (
    <Modal opened={opened} onClose={onClose} title="지식 직접 작성" size="xl">
      <form onSubmit={submit}>
        <Stack gap="sm">
          <Text size="sm" c="dimmed">
            AI가 뽑지 못하는 것을 남기는 자리입니다 — 랩에서 안 되더라고 확인한 것,
            문서에 없는 제약 같은 것. 등록하면 초안으로 들어가고, 확인 후 확정할 수 있습니다.
          </Text>
          <Group grow align="flex-start">
            <Select
              label="벤더" placeholder="선택" data={VENDORS} required
              {...form.getInputProps('vendor')}
            />
            <TextInput label="장비 모델" placeholder="예: TH4435 (선택)"
                       {...form.getInputProps('device_model')} />
            <TextInput label="소프트웨어 버전" placeholder="예: 5.2.1-P7 (선택)"
                       {...form.getInputProps('software_version')} />
          </Group>
          <TextInput
            label="제목" required
            placeholder="한 줄 요약 — 나중에 검색될 것을 생각하고 증상·장비를 담습니다"
            {...form.getInputProps('title')}
          />
          {SECTIONS.map((s) => (
            <Textarea
              key={s.key}
              label={s.label}
              description={s.hint}
              placeholder={s.hint}
              autosize
              minRows={s.rows}
              required={REQUIRED.includes(s.key)}
              styles={s.mono
                ? { input: { fontFamily: 'var(--mantine-font-family-monospace)' } }
                : undefined}
              {...form.getInputProps(s.key)}
            />
          ))}
          {error && <Text size="sm" c="red">{error}</Text>}
          <Group justify="flex-end">
            <Button variant="default" onClick={onClose} disabled={saving}>취소</Button>
            <Button type="submit" loading={saving}>등록</Button>
          </Group>
        </Stack>
      </form>
    </Modal>
  );
}
