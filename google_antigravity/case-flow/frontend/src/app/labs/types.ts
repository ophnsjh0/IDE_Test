// /api/labs/* 응답 모양. 백엔드가 EVE-NG 응답을 우리 용어로 번역해 내려주므로
// 여기에 EVE-NG의 필드 이름은 나오지 않는다.

// 노드 상태. EVE-NG가 알려주는 건 running(프로세스가 떴는가)까지고, '준비됨'은
// 장비 관리 API가 응답해야 판정할 수 있다.
//   off      꺼짐 — EVE-NG에 프로세스 없음
//   booting  프로세스는 떴는데 관리 API 무응답 (부팅 중이거나 설정이 틀림)
//   ready    관리 API가 응답
//   unknown  프로세스는 떴는데 접속 정보가 없어 확인할 수 없음
// unknown을 booting으로 뭉뚱그리면 영영 안 끝나는 것처럼 보여서 구분한다.
export type NodeState = 'off' | 'booting' | 'ready' | 'unknown';

export interface LabStatus {
  states: Record<string, NodeState>;
  counts: Partial<Record<NodeState, number>>;
  total: number;
  unprobeable: string[];   // 접속 정보가 없어 판정할 수 없는 노드
}

export const DRIVERS = [
  { value: 'none', label: '확인 안 함' },
  { value: 'a10_axapi', label: 'A10 aXAPI' },
  { value: 'arista_eapi', label: 'Arista eAPI' },
  { value: 'linux_ssh', label: 'Linux (SSH 포트)' },
];

export interface NodeAccess {
  node_name: string;
  role: string;
  mgmt_ip: string;
  driver: string;
  username: string;
  password?: string;
  has_password: boolean;
}

export interface LabNode {
  name: string;          // 우리 쪽 키 — eve_id·console 포트는 서버를 옮기면 재부여된다
  eve_id: number;
  template: string;
  image: string;
  icon: string;
  left: number;          // EVE-NG 캔버스 좌표 그대로
  top: number;
  ram: number;           // MB
  cpu: number;
  ethernet: number;
  console_url: string;
  running: boolean;
}

export interface LabNetwork {
  name: string;
  eve_id: number;
  net_type: string;      // bridge, pnet0
  left: number;
  top: number;
}

export interface LabLink {
  source: string;
  source_port: string;
  source_is_network: boolean;
  target: string;
  target_port: string;
  target_is_network: boolean;
}

export interface LabSummary {
  id: number;
  path: string;
  name: string;
  vendor: string;
  description: string;
  server: string;
  node_count: number;
  topology_synced_at: string | null;
}

export interface LabDetail extends LabSummary {
  nodes: LabNode[];
  networks: LabNetwork[];
  links: LabLink[];
}

export interface AvailableLab {
  path: string;
  file: string;
  registered: boolean;
}

// 상태는 서버(/status/)가 준다. 스냅샷만 있을 때(폴링 전)를 위한 폴백.
export function fallbackState(node: LabNode): NodeState {
  return node.running ? 'unknown' : 'off';
}

// 읽기 전용 점검 결과. 판정은 서버(코드)가 하고 화면은 보여주기만 한다.
export interface CheckResult {
  check: string;                     // '접속 정보' | '장비 확인' | '배선 대조'
  node: string;
  status: 'pass' | 'fail' | 'skip';
  detail: string;
}

export interface CheckReport {
  results: CheckResult[];
  counts: { pass: number; fail: number; skip: number };
}
