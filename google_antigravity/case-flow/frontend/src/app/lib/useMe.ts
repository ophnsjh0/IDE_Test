'use client';

import { useEffect, useState } from 'react';
import { apiFetch } from './api';

export type Role = 'viewer' | 'engineer' | 'admin';

export interface Me {
  username: string;
  name: string;
  role: Role;
  is_admin: boolean;
}

export const ROLE_LABELS: Record<Role, string> = {
  viewer: '조회자',
  engineer: '엔지니어',
  admin: '관리자',
};

// 로그인 사용자의 역할 정보. 버튼/메뉴 노출 제어용 (실제 차단은 서버가 수행).
export function useMe() {
  const [me, setMe] = useState<Me | null>(null);

  useEffect(() => {
    apiFetch('/api/auth/me/')
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (data?.authenticated) setMe(data);
      })
      .catch(() => {});
  }, []);

  const role = me?.role;
  return {
    me,
    canWrite: role === 'engineer' || role === 'admin',
    isAdmin: role === 'admin',
  };
}
