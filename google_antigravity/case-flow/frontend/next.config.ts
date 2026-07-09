import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // dev 서버에 localhost 외 주소(사내망 IP)로 접근하는 것을 허용.
  // Next 16은 여기 등록되지 않은 origin의 dev 요청을 차단한다.
  allowedDevOrigins: ['192.168.54.37', '192.168.54.*'],
};

export default nextConfig;
