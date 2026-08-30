'use client';

import { Text } from '@mantine/core';
import { apiUrl } from '../lib/api';
import type { LabDetail, LabNode, NodeState } from './types';
import { fallbackState } from './types';

// EVE-NG 좌표를 그대로 받아 SVG로 다시 그린다. 스크린샷을 가져오지 않는 이유:
// ① EVE-NG에 토폴로지 캡처 API가 없고(랩 pictures도 비어 있음) ② 이미지였다면
// 노드를 클릭하려고 좌표를 따로 매핑해야 한다. 좌표로 그리면 상태색이 실시간으로
// 바뀌고 클릭도 그냥 된다.

const STATE_COLOR: Record<NodeState, string> = {
  off: 'var(--mantine-color-gray-4)',
  booting: 'var(--mantine-color-yellow-5)',
  ready: 'var(--mantine-color-teal-5)',
  // 확인 불가는 '기동 중'과 구분되는 색이어야 한다 — 기다리면 되는 상태가 아니라
  // 접속 정보를 채워야 하는 상태다.
  unknown: 'var(--mantine-color-violet-4)',
};

const BOX_W = 108;
const BOX_H = 46;
const PAD = 60;

export default function TopologyCanvas({
  lab,
  states,
  selectedName,
  onSelect,
}: {
  lab: LabDetail | null;
  states: Record<string, NodeState>;
  selectedName: string | null;
  onSelect: (node: LabNode) => void;
}) {
  if (!lab || lab.nodes.length === 0) {
    return (
      <div style={{
        height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center',
        border: '1px dashed var(--mantine-color-gray-4)', borderRadius: 8,
      }}>
        <Text size="sm" c="dimmed" ta="center">
          {lab
            ? '등록만 되어 있고 토폴로지를 아직 수집하지 않은 랩입니다.'
            : '왼쪽 위에서 랩을 선택하세요.'}
          {lab && (
            <>
              <br />
              &quot;토폴로지 갱신&quot;을 누르면 EVE-NG에서 노드·배선을 가져옵니다.
            </>
          )}
        </Text>
      </div>
    );
  }

  const xs = lab.nodes.map((n) => n.left);
  const ys = lab.nodes.map((n) => n.top);
  const minX = Math.min(...xs) - PAD;
  const minY = Math.min(...ys) - PAD;
  const width = Math.max(...xs) - minX + BOX_W + PAD;
  const height = Math.max(...ys) - minY + BOX_H + PAD;

  const byName = new Map(lab.nodes.map((n) => [n.name, n]));
  const center = (n: LabNode) => ({ x: n.left + BOX_W / 2, y: n.top + BOX_H / 2 });

  // 관리망 연결(노드↔네트워크)은 캔버스에서 생략한다 — 모든 노드가 같은
  // pnet0으로 몰려서 그림만 어지럽고, 배선 자체는 스냅샷에 남아 있다.
  const nodeLinks = lab.links.filter((l) => !l.source_is_network && !l.target_is_network);

  return (
    <svg
      viewBox={`${minX} ${minY} ${width} ${height}`}
      style={{ width: '100%', height: '100%', display: 'block' }}
      role="img"
      aria-label={`${lab.name} 토폴로지`}
    >
      {nodeLinks.map((link, i) => {
        const a = byName.get(link.source);
        const b = byName.get(link.target);
        if (!a || !b) return null;
        const p = center(a);
        const q = center(b);
        // 양쪽 다 준비된 링크만 진하게 — 어디까지 살아났는지 배선으로도 읽힌다
        const live = (states[a.name] ?? fallbackState(a)) === 'ready'
          && (states[b.name] ?? fallbackState(b)) === 'ready';
        return (
          <g key={i}>
            <line
              x1={p.x} y1={p.y} x2={q.x} y2={q.y}
              stroke={live ? 'var(--mantine-color-teal-4)' : 'var(--mantine-color-gray-4)'}
              strokeWidth={live ? 2 : 1.5}
              strokeDasharray={live ? undefined : '4 4'}
            />
            <text
              x={(p.x + q.x) / 2} y={(p.y + q.y) / 2 - 4}
              textAnchor="middle" fontSize={11}
              fill="var(--mantine-color-gray-6)"
            >
              {link.source_port}–{link.target_port}
            </text>
          </g>
        );
      })}

      {lab.nodes.map((n) => {
        const selected = n.name === selectedName;
        const state = states[n.name] ?? fallbackState(n);
        return (
          <g
            key={n.name}
            transform={`translate(${n.left}, ${n.top})`}
            onClick={() => onSelect(n)}
            style={{ cursor: 'pointer' }}
          >
            <rect
              width={BOX_W} height={BOX_H} rx={8}
              fill="var(--mantine-color-body)"
              stroke={STATE_COLOR[state]}
              strokeWidth={selected ? 4 : 2.5}
            />
            {state === 'booting' && (
              <rect width={BOX_W} height={BOX_H} rx={8} fill="none"
                    stroke={STATE_COLOR.booting} strokeWidth={2.5}>
                <animate attributeName="opacity" values="1;0.15;1"
                         dur="1.2s" repeatCount="indefinite" />
              </rect>
            )}
            {/* 아이콘은 백엔드가 EVE-NG에서 중계한다 — 브라우저가 EVE-NG에
                직접 붙지 않게 하고, 자격증명도 나가지 않는다. */}
            {n.icon && (
              <image
                href={apiUrl(`/api/labs/icons/${encodeURIComponent(n.icon)}`)}
                x={8} y={11} width={24} height={24} preserveAspectRatio="xMidYMid meet"
              />
            )}
            <text x={n.icon ? 38 : 12} y={21} fontSize={13} fontWeight={600}
                  fill="var(--mantine-color-text)">
              {n.name}
            </text>
            <text x={n.icon ? 38 : 12} y={36} fontSize={11}
                  fill="var(--mantine-color-dimmed)">
              {n.template}
            </text>
          </g>
        );
      })}
    </svg>
  );
}
