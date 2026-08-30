'use client';

import { Text } from '@mantine/core';
import type { LabDef, LabNode, NodeState } from './mockData';

// EVE-NG 좌표를 그대로 받아 SVG로 다시 그린다. 스크린샷을 가져오지 않는 이유:
// ① EVE-NG에 토폴로지 캡처 API가 없고(랩 pictures도 비어 있음) ② 이미지였다면
// 노드를 클릭하려고 좌표를 따로 매핑해야 한다. 좌표로 그리면 상태색이 실시간으로
// 바뀌고 클릭도 그냥 된다.

const STATE_COLOR: Record<NodeState, string> = {
  off: 'var(--mantine-color-gray-4)',
  booting: 'var(--mantine-color-yellow-5)',
  ready: 'var(--mantine-color-teal-5)',
};

const KIND_FILL: Record<string, string> = {
  lb: 'var(--mantine-color-blue-1)',
  switch: 'var(--mantine-color-indigo-1)',
  server: 'var(--mantine-color-gray-1)',
  router: 'var(--mantine-color-grape-1)',
};

const BOX_W = 108;
const BOX_H = 46;
const PAD = 60;

export default function TopologyCanvas({
  lab,
  selectedId,
  onSelect,
}: {
  lab: LabDef;
  selectedId: number | null;
  onSelect: (node: LabNode) => void;
}) {
  if (lab.nodes.length === 0) {
    return (
      <div style={{
        height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center',
        border: '1px dashed var(--mantine-color-gray-4)', borderRadius: 8,
      }}>
        <Text size="sm" c="dimmed" ta="center">
          등록만 되어 있고 토폴로지를 아직 수집하지 않은 랩입니다.
          <br />
          &quot;토폴로지 갱신&quot;을 누르면 EVE-NG에서 노드·배선을 가져옵니다.
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

  const byId = new Map(lab.nodes.map((n) => [n.id, n]));
  const center = (n: LabNode) => ({ x: n.left + BOX_W / 2, y: n.top + BOX_H / 2 });

  return (
    <svg
      viewBox={`${minX} ${minY} ${width} ${height}`}
      style={{ width: '100%', height: '100%', display: 'block' }}
      role="img"
      aria-label={`${lab.name} 토폴로지`}
    >
      {lab.links.map((link, i) => {
        const a = byId.get(link.from);
        const b = byId.get(link.to);
        if (!a || !b) return null;
        const p = center(a);
        const q = center(b);
        // 양쪽 다 준비된 링크만 진하게 — 어디까지 살아났는지 배선으로도 읽힌다
        const live = a.state === 'ready' && b.state === 'ready';
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
              {link.fromPort}–{link.toPort}
            </text>
          </g>
        );
      })}

      {lab.nodes.map((n) => {
        const selected = n.id === selectedId;
        return (
          <g
            key={n.id}
            transform={`translate(${n.left}, ${n.top})`}
            onClick={() => onSelect(n)}
            style={{ cursor: 'pointer' }}
          >
            <rect
              width={BOX_W} height={BOX_H} rx={8}
              fill={KIND_FILL[n.kind] ?? KIND_FILL.server}
              stroke={STATE_COLOR[n.state]}
              strokeWidth={selected ? 4 : 2.5}
            />
            {/* 기동 중은 깜빡여서 "아직 기다리는 중"임을 한눈에 보이게 한다 */}
            {n.state === 'booting' && (
              <rect width={BOX_W} height={BOX_H} rx={8} fill="none"
                    stroke={STATE_COLOR.booting} strokeWidth={2.5}>
                <animate attributeName="opacity" values="1;0.15;1" dur="1.2s" repeatCount="indefinite" />
              </rect>
            )}
            <text x={BOX_W / 2} y={20} textAnchor="middle" fontSize={13} fontWeight={600}
                  fill="var(--mantine-color-dark-6)">
              {n.name}
            </text>
            <text x={BOX_W / 2} y={36} textAnchor="middle" fontSize={11}
                  fill="var(--mantine-color-gray-6)">
              {n.template}
            </text>
          </g>
        );
      })}
    </svg>
  );
}
