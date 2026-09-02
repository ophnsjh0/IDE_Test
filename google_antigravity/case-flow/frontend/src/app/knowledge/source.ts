// 지식의 출처 = 신뢰도의 서열. 목록과 상세가 같은 말로 표시해야 하므로 한 곳에 둔다.
//
//   case(벤더가 실제 해결한 기록) > lab(우리 랩에서 실제로 돌려본 결과)
//   > chat(AI 추론) · manual(엔지니어가 손으로 적음)
//
// 빈 값은 '불명' — source 칸이 생기기 전에 만들어졌고 출처 객체도 이미 지워진
// 옛 지식이다. 어느 쪽이었는지 알 수 없어 추측해 채우지 않았다.
export type KnowledgeSource = 'case' | 'lab' | 'chat' | 'manual' | '';

export const SOURCE_LABEL: Record<KnowledgeSource, string> = {
  case: '벤더 케이스',
  lab: '랩 재현',
  chat: 'AI 대화',
  manual: '직접 작성',
  '': '출처 불명',
};

// 목록의 좁은 칸용 — 상세와 달리 한 칸에 들어가야 한다
export const SOURCE_SHORT: Record<KnowledgeSource, string> = {
  case: '케이스',
  lab: '랩',
  chat: 'AI 대화',
  manual: '직접',
  '': '—',
};

export const SOURCE_COLOR: Record<KnowledgeSource, string> = {
  case: 'blue',
  lab: 'teal',
  chat: 'grape',
  manual: 'gray',
  '': 'gray',
};

// 신뢰도 한 줄 설명 — 화면에서 왜 색이 다른지 사람이 알 수 있게
export const SOURCE_NOTE: Record<KnowledgeSource, string> = {
  case: '벤더가 실제로 해결한 기록입니다.',
  lab: '우리 랩에서 실제로 실행해 검증한 결과입니다.',
  chat: 'AI 답변에서 추출한 것으로, 벤더 확인을 거치지 않았습니다.',
  manual: '엔지니어가 직접 작성했습니다.',
  '': '출처 기록이 남기 전에 만들어졌거나 출처가 삭제된 지식입니다.',
};

export function sourceOf(item: {
  source?: string;
  source_case?: unknown;
  source_session?: unknown;
}): KnowledgeSource {
  if (item.source) return item.source as KnowledgeSource;
  // source 칸이 비어 있어도 FK가 살아 있으면 그쪽을 믿는다 — 마이그레이션
  // 백필과 같은 규칙이라, 백필 전 데이터를 보는 화면도 어긋나지 않는다.
  if (item.source_case) return 'case';
  if (item.source_session) return 'chat';
  return '';
}
