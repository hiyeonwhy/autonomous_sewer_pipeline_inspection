import tailwindcss from '@tailwindcss/vite';
import react from '@vitejs/plugin-react';
import path from 'path';
import fs from 'fs';
import {defineConfig, type Plugin} from 'vite';

// maplibre-gl 6 은 워커 경로를 런타임에 조립합니다:
//   new URL(`./maplibre-gl-worker.mjs`, import.meta.url)
// 문자열이 정적으로 드러나지 않아 rollup 이 워커를 청크로 내보내지 못하고,
// 프로덕션 번들에서는 import.meta.url 이 /assets/index-*.js 로 풀려
// /assets/maplibre-gl-worker.mjs 가 404 가 됩니다. 그러면 GeoJSON 소스가 영영
// 로드되지 않아 배경지도만 뜨고 폴리곤이 하나도 그려지지 않습니다
// (2026-08-03 배포에서 실제로 발생 — 개발 서버는 아래 optimizeDeps.exclude 덕에
// node_modules 원본을 서빙해서 형제 파일이 있으니 정상이라 오래 드러나지 않았습니다).
//
// 워커는 maplibre-gl-shared.mjs 를 상대 경로로 import 하므로 둘을 **같은
// 디렉터리**에 함께 복사해야 맞물립니다. 해시를 붙이지 않는 이유도 그것입니다.
// 사용하는 쪽은 PriorityMap 의 setWorkerUrl 입니다.
const MAPLIBRE_WORKER_FILES = ['maplibre-gl-worker.mjs', 'maplibre-gl-shared.mjs'];
export const MAPLIBRE_WORKER_DIR = 'maplibre';

const maplibreWorkerAssets = (): Plugin => ({
  name: 'maplibre-worker-assets',
  apply: 'build',
  generateBundle() {
    const dist = path.resolve(__dirname, 'node_modules/maplibre-gl/dist');
    for (const name of MAPLIBRE_WORKER_FILES) {
      const from = path.join(dist, name);
      if (!fs.existsSync(from)) {
        // 조용히 넘어가면 배포 후에야 404 로 드러나므로 빌드를 멈춥니다.
        this.error(`maplibre 워커 파일을 찾을 수 없습니다: ${from}`);
      }
      this.emitFile({
        type: 'asset',
        fileName: `${MAPLIBRE_WORKER_DIR}/${name}`,
        source: fs.readFileSync(from),
      });
    }
  },
});

const localMediaPlugin = () => ({
  name: 'local-media-and-capture',
  configureServer(server: any) {
    server.middlewares.use('/local-media', (req: any, res: any, next: any) => {
      const parts = decodeURIComponent((req.url || '').split('?')[0]).split('/').filter(Boolean);
      if (parts.length !== 2 || !['images', 'find_images'].includes(parts[0])) return next();

      const baseDir = path.resolve(__dirname, parts[0]);
      const filePath = path.resolve(baseDir, parts[1]);
      if (!filePath.startsWith(`${baseDir}${path.sep}`) || !fs.existsSync(filePath)) return next();

      const ext = path.extname(filePath).toLowerCase();
      const contentTypes: Record<string, string> = {
        '.mp4': 'video/mp4', '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp'
      };
      res.setHeader('Content-Type', contentTypes[ext] || 'application/octet-stream');
      fs.createReadStream(filePath).pipe(res);
    });
  },
});

const BACKEND_TARGET =
  process.env.VITE_BACKEND_TARGET || 'https://i15c102.p.ssafy.io';

export default defineConfig(() => {
  return {
    plugins: [react(), tailwindcss(), localMediaPlugin(), maplibreWorkerAssets()],
    // maplibre-gl 은 사전번들(esbuild)을 거치면 내부 웹워커가 죽어서 GeoJSON
    // 소스가 영영 로드되지 않습니다 (레이어는 있는데 타일이 0개). 원본 ESM 을
    // 그대로 서빙하면 정상.
    // 프로덕션 빌드도 같은 증상이 납니다 — 원인은 다릅니다(워커 청크 미생성).
    // 위 maplibreWorkerAssets 플러그인과 PriorityMap 의 setWorkerUrl 이 그쪽을 답니다.
    optimizeDeps: { exclude: ['maplibre-gl'] },
    resolve: {
      alias: {
        '@': path.resolve(__dirname, '.'),
      },
    },
    server: {
      port: 3000,
      strictPort: true,
      proxy: {
        // 모든 API 를 배포 서버로 보냅니다 — 우선순위 API 가 배포되면서
        // 우선순위만 로컬(8001)로 빼던 블록을 지웠습니다. 그 블록이 있으면
        // 로컬 백엔드를 안 띄운 팀원은 지도 탭만 못 봅니다.
        //
        // 백엔드를 직접 고칠 때는 대상을 로컬로 돌립니다:
        //   VITE_BACKEND_TARGET=http://127.0.0.1:8001 npm run dev
        '/backend-api': {
          target: BACKEND_TARGET,
          changeOrigin: true,
          secure: BACKEND_TARGET.startsWith('https'),
          rewrite: (requestPath) =>
            requestPath.replace(/^\/backend-api/, '/api'),
        },
      },
      // HMR is disabled in AI Studio via DISABLE_HMR env var.
      // Do not modifyâfile watching is disabled to prevent flickering during agent edits.
      hmr: process.env.DISABLE_HMR !== 'true',
      // Disable file watching when DISABLE_HMR is true to save CPU during agent edits.
      watch: process.env.DISABLE_HMR === 'true' ? null : {},
    },
  };
});
