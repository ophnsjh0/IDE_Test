// /api/labs/* 응답 모양. 백엔드가 EVE-NG 응답을 우리 용어로 번역해 내려주므로
// 여기에 EVE-NG의 필드 이름은 나오지 않는다.

// 노드 상태는 3단계다. EVE-NG가 알려주는 건 running(프로세스가 떴는가)까지고,
// '준비됨'은 장비 관리 API가 응답해야 판정할 수 있어 Step 2에서 붙는다.
export type NodeState = 'off' | 'booting' | 'ready';

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

export function nodeState(node: LabNode): NodeState {
  // Step 2에서 관리 API 프로브가 붙으면 'ready'가 여기서 갈린다.
  return node.running ? 'booting' : 'off';
}
