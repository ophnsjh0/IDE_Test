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
  // 우리 쪽 키 — eve_id·console 포트는 서버를 옮기면 재부여된다.
  // EVE-NG는 같은 이름의 노드를 허용해서, 이름이 겹칠 때만 '이름#eve_id'로 갈라진다.
  name: string;
  display_name: string;  // EVE-NG 화면에 뜨는 원래 이름
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
  name: string;          // 노드와 같은 규칙의 유일 키 ('Net'이 여럿일 수 있다)
  display_name: string;
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

export interface Blueprint {
  id: number;
  name: string;
  description: string;
  steps: number;
  problems: string[];        // 실행 전에 못 돌리는 이유 (역할 미매핑 등)
}

export interface RunStep {
  seq: number;
  phase: 'precheck' | 'apply' | 'verify' | 'rollback';
  node: string;
  label: string;
  status: string;            // pass | fail | skip | error
  detail: string;
}

export interface RunCase {
  id: number;
  case_id: string;         // C-1001
  summary: string;
  vendor: string;
}

export interface Run {
  id: number;
  lab: { id: number; name: string };
  // 무엇을 재현하려던 실행인가. 케이스가 지워졌으면 null이 된다.
  case: RunCase | null;
  blueprint: string;
  status: 'running' | 'passed' | 'failed' | 'rolled_back' | 'error';
  started_at: string;
  finished_at: string | null;
  topology_synced_at: string | null;
  steps: RunStep[];
  pending_rollback: number;  // 되돌리지 않고 장비에 남아 있는 것
}

export interface Proposal {
  id: number;
  title: string;
  reason: string;
  steps: {
    role: string; label?: string;
    apply: string[];
    verify: { command: string; contains?: string; not_contains?: string };
    rollback: string[];
  }[];
  status: 'pending' | 'approved' | 'rejected';
}

export interface ChatReply {
  reply: string;
  tools: { name: string }[];
  model: string;
  proposals: Proposal[];
  usage: { input: number; output: number };
}

// 검증된 명령 사전 — 랩에 매이지 않는다. (벤더, OS 버전)이 키라서 어느 랩에서
// 확인됐든 같은 장비면 그대로 쓸모가 있다.
export interface Recipe {
  id: number;
  vendor: string;
  os_version: string;          // '' = 버전 미상
  purpose: string;
  apply: string[];
  verify: { command: string; contains: string; not_contains: string };
  rollback: string[];
  // verified=돌려서 통과 / untested=사람이 넣어둠 / failed=그 버전에서 실패
  outcome: 'verified' | 'untested' | 'failed';
  verified_count: number;
  failed_count: number;
  last_failure: string;
  device_model: string;
  source: 'run' | 'manual';
  last_run: number | null;
}

// IP 경고 — 시스템은 배정하지 않고 경고만 한다. 사람이 고른 IP를 막지 않는
// 이유는 랩이 일부러 이상한 값을 넣어보는 곳이기도 해서다.
export interface IpWarning {
  node: string;
  ip: string;
  message: string;
}

export interface AccessPayload {
  rows: NodeAccess[];
  warnings: IpWarning[];
  free_ips: string[];            // 서버 전체에서 아직 아무도 안 쓰는 관리 IP
  data_subnet: string;           // 이 랩에 떼어 준 시험 트래픽 대역
  suggested_data_subnet: string; // 아직 아무 랩도 안 쓰는 다음 대역
}
