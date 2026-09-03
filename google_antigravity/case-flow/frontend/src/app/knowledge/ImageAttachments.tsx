'use client';

import { useRef, useState } from 'react';
import {
  ActionIcon,
  Button,
  Group,
  Loader,
  Modal,
  Paper,
  Select,
  Stack,
  Text,
  TextInput,
  Tooltip,
} from '@mantine/core';
import { IconPhotoPlus, IconTrash, IconZoomIn } from '@tabler/icons-react';
import { apiFetch, apiUrl } from '../lib/api';
import { SECTIONS, type SectionKey } from './sections';

export interface KnowledgeImage {
  filename: string;       // "<지식 id>/<uuid>.png" — 서버가 지어준 이름
  original_name: string;  // 올린 사람이 보던 이름 (표시·다운로드용)
  caption: string;
  section: SectionKey | ''; // 붙는 본문 칸. 빈 값이면 맨 아래 묶음
  uploaded_by: string;
  uploaded_at: string;
  size_bytes: number;
}

// 서버가 허용하는 것과 같은 목록. 여기서 미리 걸러야 붙여넣기가 엉뚱한 파일을
// 올리고 400을 받는 왕복을 피한다.
const ACCEPT = 'image/png,image/jpeg,image/webp';

export function imageUrl(image: KnowledgeImage) {
  return apiUrl(`/api/knowledge/images/${image.filename}`);
}

/** 보기 화면의 그림 묶음 — 눌러서 원본 크기로 연다. */
export function ImageGallery({ images }: { images: KnowledgeImage[] }) {
  const [opened, setOpened] = useState<KnowledgeImage | null>(null);
  if (images.length === 0) return null;

  return (
    <>
      <Group gap="sm" align="flex-start" mt={8}>
        {images.map((image) => (
          <Stack key={image.filename} gap={4} style={{ maxWidth: 320 }}>
            <Paper
              withBorder radius="md" p={4}
              style={{ cursor: 'zoom-in', lineHeight: 0, position: 'relative' }}
              onClick={() => setOpened(image)}
            >
              {/* 구성도는 가로로 긴 그림이 많아 높이로 맞춘다 */}
              <img
                src={imageUrl(image)}
                alt={image.caption || image.original_name}
                style={{ maxHeight: 220, maxWidth: '100%', borderRadius: 4 }}
              />
              <IconZoomIn
                size={16}
                style={{
                  position: 'absolute', right: 8, bottom: 8, opacity: 0.55,
                  background: 'var(--mantine-color-body)', borderRadius: 3,
                }}
              />
            </Paper>
            {image.caption && (
              <Text size="xs" c="dimmed" style={{ lineHeight: 1.4 }}>
                {image.caption}
              </Text>
            )}
          </Stack>
        ))}
      </Group>

      <Modal
        opened={opened !== null}
        onClose={() => setOpened(null)}
        size="90%"
        title={opened?.caption || opened?.original_name}
      >
        {opened && (
          <Stack gap="xs">
            <img
              src={imageUrl(opened)}
              alt={opened.caption || opened.original_name}
              style={{ width: '100%', height: 'auto' }}
            />
            <Text size="xs" c="dimmed">
              {opened.original_name} · {opened.uploaded_by} 올림 ·{' '}
              {opened.uploaded_at.slice(0, 10)}
              {' · '}
              <a href={imageUrl(opened)} target="_blank" rel="noreferrer">새 탭에서 원본 열기</a>
            </Text>
          </Stack>
        )}
      </Modal>
    </>
  );
}

/**
 * 수정 화면의 그림 관리 — 올리기·설명·붙는 칸·삭제.
 *
 * 본문 텍스트와 달리 "저장" 버튼을 기다리지 않고 누르는 즉시 서버에 반영한다.
 * 파일 업로드를 폼 저장에 묶으면 저장이 실패했을 때 올라간 파일만 남아
 * 화면과 디스크가 어긋난다.
 */
export function ImageManager({
  knowledgeId,
  images,
  onChange,
}: {
  knowledgeId: number | string;
  images: KnowledgeImage[];
  onChange: (images: KnowledgeImage[]) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const fileInput = useRef<HTMLInputElement>(null);

  const sectionOptions = [
    { value: '', label: '맨 아래 (칸 지정 안 함)' },
    ...SECTIONS.map((s) => ({ value: s.key, label: s.label })),
  ];

  const upload = async (files: File[]) => {
    if (files.length === 0) return;
    setBusy(true);
    setError('');
    try {
      let latest = images;
      for (const file of files) {
        const body = new FormData();
        body.append('file', file);
        const res = await apiFetch(`/api/knowledge/${knowledgeId}/images/`, {
          method: 'POST',
          body,
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
        latest = data.images;
      }
      onChange(latest);
    } catch (e) {
      setError(e instanceof Error ? e.message : '이미지를 올리지 못했습니다.');
    } finally {
      setBusy(false);
      if (fileInput.current) fileInput.current.value = ''; // 같은 파일 다시 고를 수 있게
    }
  };

  const update = async (filename: string, patch: Partial<KnowledgeImage>) => {
    setError('');
    const res = await apiFetch(`/api/knowledge/${knowledgeId}/images/`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ filename, ...patch }),
    });
    const data = await res.json().catch(() => ({}));
    if (res.ok) onChange(data.images);
    else setError(data.error || '수정하지 못했습니다.');
  };

  const remove = async (image: KnowledgeImage) => {
    if (!window.confirm(`이 이미지를 지울까요? 되돌릴 수 없습니다.`)) return;
    setError('');
    const res = await apiFetch(`/api/knowledge/${knowledgeId}/images/`, {
      method: 'DELETE',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ filename: image.filename }),
    });
    const data = await res.json().catch(() => ({}));
    if (res.ok) onChange(data.images);
    else setError(data.error || '삭제하지 못했습니다.');
  };

  return (
    <Stack gap="xs">
      <div>
        <Text size="sm" fw={500}>구성도 · 이미지</Text>
        <Text size="xs" c="dimmed">
          PNG · JPG · WEBP, 장당 5MB까지. 캡처는 아래 영역에 바로 붙여넣기(Ctrl+V)할 수
          있습니다. 올린 즉시 저장되며 본문 &ldquo;저장&rdquo;과 무관합니다.
        </Text>
      </div>

      {images.map((image) => (
        <Paper key={image.filename} withBorder p="xs" radius="md">
          <Group align="flex-start" wrap="nowrap" gap="sm">
            <img
              src={imageUrl(image)}
              alt={image.caption || image.original_name}
              style={{ width: 96, height: 72, objectFit: 'cover', borderRadius: 4,
                       flexShrink: 0, border: '1px solid var(--mantine-color-gray-3)' }}
            />
            <Stack gap={6} style={{ flex: 1, minWidth: 0 }}>
              <TextInput
                size="xs"
                placeholder="설명 (예: A10 One-Arm 구성도)"
                defaultValue={image.caption}
                // 타이핑마다 서버를 때리지 않는다 — 포커스를 뗄 때 한 번만
                onBlur={(e) => {
                  const caption = e.currentTarget.value;
                  if (caption !== image.caption) update(image.filename, { caption });
                }}
              />
              <Group gap="xs" wrap="nowrap">
                <Select
                  size="xs"
                  style={{ flex: 1 }}
                  data={sectionOptions}
                  value={image.section}
                  allowDeselect={false}
                  onChange={(section) =>
                    update(image.filename, { section: (section ?? '') as SectionKey | '' })}
                />
                <Text size="xs" c="dimmed" style={{ whiteSpace: 'nowrap' }}>
                  {Math.round(image.size_bytes / 1024)} KB
                </Text>
                <Tooltip label="삭제" withArrow>
                  <ActionIcon color="red" variant="light" size="sm"
                              onClick={() => remove(image)}>
                    <IconTrash size={14} />
                  </ActionIcon>
                </Tooltip>
              </Group>
            </Stack>
          </Group>
        </Paper>
      ))}

      {/* 붙여넣기를 받으려면 포커스를 받을 수 있어야 한다 — tabIndex를 준 이유 */}
      <Paper
        withBorder radius="md" p="md" tabIndex={0}
        style={{ borderStyle: 'dashed', textAlign: 'center', outlineOffset: 2 }}
        onPaste={(e) => {
          const files = Array.from(e.clipboardData.files)
            .filter((f) => ACCEPT.includes(f.type));
          if (files.length === 0) return;
          e.preventDefault();
          upload(files);
        }}
      >
        <Stack gap={6} align="center">
          {busy ? <Loader size="sm" /> : (
            <>
              <Button
                size="xs" variant="light"
                leftSection={<IconPhotoPlus size={14} />}
                onClick={() => fileInput.current?.click()}
              >
                이미지 선택
              </Button>
              <Text size="xs" c="dimmed">
                또는 여기를 클릭하고 Ctrl+V로 붙여넣기
              </Text>
            </>
          )}
        </Stack>
        <input
          ref={fileInput}
          type="file"
          accept={ACCEPT}
          multiple
          hidden
          onChange={(e) => upload(Array.from(e.currentTarget.files ?? []))}
        />
      </Paper>

      {error && <Text size="xs" c="red">{error}</Text>}
    </Stack>
  );
}
