// 본문 8칸의 표시 순서·라벨·설명. 보기 화면·수정 폼·직접 작성 폼이 같은 목록에서
// 나오므로 필드를 추가할 때 한 군데만 고치면 된다. 순서는 읽는 흐름 그대로 —
// 어떤 환경에서(환경) 무엇이 잘못됐고(문제) 어떻게 좁혀(진단) 원인을 찾아(원인)
// 무엇을 했고(해결) 어떻게 확인하며(검증) 무엇을 조심하는지(주의).
export type SectionKey =
  | 'environment' | 'problem' | 'diagnosis' | 'root_cause'
  | 'resolution' | 'verification' | 'caveats' | 'related_refs';

export interface Section {
  key: SectionKey;
  label: string;
  hint: string;
  rows: number;
  mono?: boolean;   // 명령어·식별자가 들어가는 칸은 고정폭으로
  cards?: boolean;  // 한 줄에 하나씩인 항목은 보기 화면에서 카드로 (공식 문서 근거와 동일)
  tone?: 'warn';    // 주의사항은 경고 색 강조
}

export const SECTIONS: Section[] = [
  { key: 'environment', label: '환경 · 전제 조건', hint: '구성 방식, 토폴로지, 장비 모델, 버전, 라이선스', rows: 3 },
  { key: 'problem', label: '문제 상황', hint: '어떤 조건에서 무엇이 잘못됐는지', rows: 4 },
  { key: 'diagnosis', label: '진단 절차', hint: '원인을 좁혀간 명령·로그·테스트를 순서대로', rows: 4 },
  { key: 'root_cause', label: '근본 원인', hint: '밝혀진 원인 (모르면 비워둡니다)', rows: 3 },
  { key: 'resolution', label: '해결 조치', hint: 'CLI 명령어·설정 라인·패치 버전을 그대로', rows: 6, mono: true },
  { key: 'verification', label: '검증 방법', hint: '조치 후 어떤 명령의 어떤 출력을 확인하는지', rows: 3, mono: true },
  { key: 'caveats', label: '주의사항', hint: '부작용, 재발 조건, 적용 범위 밖인 상황', rows: 3, tone: 'warn' },
  { key: 'related_refs', label: '관련 참조', hint: '벤더 버그 ID·케이스 번호·문서명 (한 줄에 하나)', rows: 2, mono: true, cards: true },
];
