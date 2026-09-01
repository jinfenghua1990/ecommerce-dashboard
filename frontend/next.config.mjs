/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  async rewrites() {
    return [
      // 健康探针：对外暴露 api 的 /healthz，供 8888 聚合中心读取真实健康度
      {
        source: "/healthz",
        destination: "http://api:8000/healthz",
      },
      {
        source: "/api/:path*",
        destination: "http://api:8000/api/:path*",
      },
    ];
  },
};

export default nextConfig;
