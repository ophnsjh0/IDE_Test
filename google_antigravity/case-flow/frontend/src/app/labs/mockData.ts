// /labs 화면 설계 검토용 목 데이터. 백엔드 연동 전이라 여기서만 상태를 만든다.
//
// 좌표·링크·자원값은 EVE-NG(192.168.74.130)에서 실제로 뽑은 값이다 — 가짜 배치로
// 그리면 실제 연동 때 화면이 전혀 달라 보이므로, 처음부터 진짜 토폴로지로 확인한다.

export type NodeState = 'off' | 'booting' | 'ready';

// EVE-NG 아이콘 파일명 대신 굵은 분류만 둔다. 실제 연동 때는 백엔드가 EVE-NG의
// /images/icons/<icon>을 프록시해 진짜 아이콘을 쓰면 된다.
export type NodeKind = 'lb' | 'switch' | 'server' | 'router';

export interface LabNode {
  id: number;
  name: string;
  kind: NodeKind;
  template: string;
  left: number;      // EVE-NG 캔버스 좌표 그대로
  top: number;
  ram: number;       // MB
  cpu: number;
  console: string;   // telnet://... 또는 vnc://...
  state: NodeState;
}

export interface LabLink {
  from: number;
  fromPort: string;
  to: number;
  toPort: string;
}

export interface LabDef {
  file: string;        // EVE-NG .unl 파일명 (식별자)
  name: string;        // 화면 표시 이름
  vendor: string;      // 셀렉터 그룹
  description: string;
  nodes: LabNode[];
  links: LabLink[];
}

function node(
  id: number, name: string, kind: NodeKind, template: string,
  left: number, top: number, ram: number, cpu: number, port: number,
  state: NodeState = 'off',
): LabNode {
  return {
    id, name, kind, template, left, top, ram, cpu, state,
    console: `${kind === 'server' ? 'vnc' : 'telnet'}://192.168.74.130:${port}`,
  };
}

// AI-LAB-A10-OneArm — 실제 랩 그대로 (노드 9, 링크 20, RAM 45GB, vCPU 18).
// A10 이중화 쌍이 Arista 코어 2대에 물리고, 액세스 2대 아래 서버 2대·클라이언트 1대.
const a10OneArm: LabDef = {
  file: 'AI-LAB-A10-OneArm.unl',
  name: 'A10 One-Arm SLB',
  vendor: 'A10',
  description: 'A10 이중화 + Arista 코어/액세스 + 실서버 2대, 클라이언트 1대',
  nodes: [
    node(1, 'Arista_1', 'switch', 'veos', 330, 381, 4096, 1, 32769),
    node(2, 'Arista_2', 'switch', 'veos', 555, 381, 4096, 1, 32770),
    node(3, 'A10_1', 'lb', 'a10', 150, 243, 8192, 4, 32771),
    node(4, 'A10_2', 'lb', 'a10', 720, 249, 8192, 4, 32772),
    node(5, 'Arista_3', 'switch', 'veos', 441, 540, 4096, 1, 32773),
    node(6, 'Server_1', 'server', 'linux', 339, 669, 4096, 2, 32774),
    node(7, 'Server_2', 'server', 'linux', 555, 675, 4096, 2, 32775, 'ready'),
    node(8, 'Arista_4', 'switch', 'veos', 444, 177, 4096, 1, 32776, 'ready'),
    node(9, 'Client', 'server', 'linux', 450, 24, 4096, 2, 32777, 'ready'),
  ],
  links: [
    { from: 2, fromPort: 'Eth8', to: 1, toPort: 'Eth8' },
    { from: 3, fromPort: 'E1', to: 1, toPort: 'Eth1' },
    { from: 4, fromPort: 'E1', to: 2, toPort: 'Eth1' },
    { from: 4, fromPort: 'E2', to: 3, toPort: 'E2' },
    { from: 5, fromPort: 'Eth1', to: 1, toPort: 'Eth2' },
    { from: 5, fromPort: 'Eth2', to: 2, toPort: 'Eth2' },
    { from: 6, fromPort: 'e0', to: 5, toPort: 'Eth3' },
    { from: 7, fromPort: 'e0', to: 5, toPort: 'Eth4' },
    { from: 8, fromPort: 'Eth1', to: 1, toPort: 'Eth3' },
    { from: 8, fromPort: 'Eth2', to: 2, toPort: 'Eth3' },
    { from: 9, fromPort: 'e0', to: 8, toPort: 'Eth3' },
  ],
};

const tacacs: LabDef = {
  file: 'TEST_A10_TACACS.unl',
  name: 'A10 TACACS+ 연동',
  vendor: 'A10',
  description: '단일 A10에 외부 인증 연동 검증',
  nodes: [node(1, 'A10', 'lb', 'a10', 400, 300, 8192, 4, 32801)],
  links: [],
};

const autocheck: LabDef = {
  file: 'TEST_Autocheck_Arista-Aruba.unl',
  name: 'Arista · Aruba 자동 점검',
  vendor: 'Arista / Aruba',
  description: 'ArubaCX 3대 + vEOS 3대 백본, ISP 2회선',
  nodes: [
    node(1, 'ArubaCX_BB1', 'switch', 'arubacx', 240, 240, 4096, 2, 32810),
    node(2, 'ArubaCX_BB2', 'switch', 'arubacx', 240, 420, 4096, 2, 32811),
    node(3, 'ArubaCX_SW1', 'switch', 'arubacx', 240, 600, 4096, 2, 32812),
    node(4, 'vEOS_BB1', 'switch', 'veos', 600, 240, 4096, 1, 32813),
    node(5, 'vEOS_BB2', 'switch', 'veos', 600, 420, 4096, 1, 32814),
    node(6, 'vEOS_SW1', 'switch', 'veos', 600, 600, 4096, 1, 32815),
    node(7, 'ISP1', 'router', 'iol', 420, 90, 1024, 1, 32816),
    node(8, 'ISP2', 'router', 'iol', 420, 690, 1024, 1, 32817, 'ready'),
  ],
  links: [
    { from: 1, fromPort: 'Eth1', to: 2, toPort: 'Eth1' },
    { from: 2, fromPort: 'Eth2', to: 3, toPort: 'Eth1' },
    { from: 4, fromPort: 'Eth1', to: 5, toPort: 'Eth1' },
    { from: 5, fromPort: 'Eth2', to: 6, toPort: 'Eth1' },
    { from: 1, fromPort: 'Eth3', to: 4, toPort: 'Eth3' },
    { from: 2, fromPort: 'Eth3', to: 5, toPort: 'Eth3' },
    { from: 7, fromPort: 'e0/0', to: 1, toPort: 'Eth4' },
    { from: 8, fromPort: 'e0/0', to: 3, toPort: 'Eth4' },
  ],
};

// 나머지 등록 랩은 아직 상세를 안 넣었다 — 셀렉터에서 고를 수 있고 화면이
// "등록됨, 토폴로지 미수집" 상태로 뜨는 것까지 확인하는 용도.
function stub(file: string, name: string, vendor: string, description: string): LabDef {
  return { file, name, vendor, description, nodes: [], links: [] };
}

// 초기 8개. EVE-NG에는 21개가 있지만 전부 노출하지 않는다 — 다른 사람 작업용 랩이
// 섞여 있어서, 관리자가 등록한 것만 메뉴에 올린다는 전제다.
export const LABS: LabDef[] = [
  a10OneArm,
  tacacs,
  stub('LAB_A10_GSLB_Soop.unl', 'A10 GSLB 위임', 'A10', 'CNAME 위임 기반 GSLB'),
  stub('LAB_A10_L3DSR.unl', 'A10 L3 DSR', 'A10', 'IP-in-IP 터널 DSR 구성'),
  stub('TEST_A10_HTTP1_to_HTTP2.unl', 'A10 HTTP/1.1 → HTTP/2', 'A10', '혼합 프로토콜 connection-reuse'),
  autocheck,
  stub('LAB_Arista_EVPN.unl', 'Arista EVPN', 'Arista / Aruba', 'VXLAN EVPN 백본'),
  stub('LAB_juniper_VRRP.unl', 'Juniper VRRP', 'Juniper', 'VRRP 이중화 검증'),
];

// ---------------------------------------------------------------- 실행 진행

export type StepState = 'done' | 'running' | 'pending' | 'failed';

export interface RunStep {
  label: string;
  state: StepState;
  detail?: string;
  elapsed?: string;
}

export const MOCK_STEPS: RunStep[] = [
  { label: '토폴로지 확인', state: 'done', detail: '노드 9 · 링크 11 · EVE-NG 배선과 일치', elapsed: '0.4s' },
  { label: '사전 상태 수집', state: 'done', detail: 'A10 2대 aXAPI 응답, Arista 4대 eAPI 응답', elapsed: '1.2s' },
  { label: '설정 적용', state: 'running', detail: 'slb server / virtual-server 생성 중 (3/7)' },
  { label: '검증', state: 'pending' },
  { label: '롤백', state: 'pending' },
];

export interface ChatMessage {
  role: 'user' | 'assistant';
  text: string;
  // 설정을 바꾸는 제안은 사람이 승인해야 실행된다 (코드로 강제하는 게이트).
  proposal?: { title: string; commands: string[] };
}

export const MOCK_CHAT: ChatMessage[] = [
  { role: 'user', text: '지금 랩 상태 알려줘' },
  {
    role: 'assistant',
    text: '준비된 노드는 3대입니다 — Client, Arista_4, Server_2. '
      + 'A10_1/A10_2를 포함한 6대가 꺼져 있어 One-Arm SLB 테스트는 아직 돌릴 수 없습니다. '
      + '왼쪽에서 전체 켜기를 누르시면 준비 상태를 여기서 계속 알려드리겠습니다.',
  },
  { role: 'user', text: 'VIP 하나 만들어서 분산되는지 보고 싶어' },
  {
    role: 'assistant',
    text: 'A10_1에 실서버 2대와 VIP를 만드는 설정을 준비했습니다. '
      + '적용 후 Client에서 요청을 보내 Server_1/Server_2에 분산되는지 확인하고, '
      + '끝나면 만든 객체를 지워 원래대로 되돌립니다. 승인하시면 진행합니다.',
    proposal: {
      title: 'A10_1 · SLB 객체 생성 (5개)',
      commands: [
        'slb server rs1 10.10.10.11',
        'slb server rs2 10.10.10.12',
        'slb service-group sg-http tcp',
        'slb virtual-server vip1 10.10.10.100',
        '  port 80 http',
      ],
    },
  },
];
