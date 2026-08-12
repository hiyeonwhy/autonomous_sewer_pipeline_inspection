const DEFAULT_CAMERA_BASE_URL = 'http://192.168.100.246:8081'

export function cameraStreamUrl(env = {}) {
  return env.VITE_CAMERA_STREAM_URL || `${DEFAULT_CAMERA_BASE_URL}/stream.mjpg`
}

export function cameraHealthUrl(env = {}) {
  return env.VITE_CAMERA_HEALTH_URL || `${DEFAULT_CAMERA_BASE_URL}/health`
}

export async function readCameraHealth(fetchImpl = fetch, env = {}) {
  try {
    const response = await fetchImpl(cameraHealthUrl(env), { cache: 'no-store' })
    const payload = await response.json()
    const connected = Boolean(response.ok && payload.ok)

    return {
      connected,
      camera: payload.camera ?? null,
      frames: Number.isFinite(payload.frames) ? payload.frames : 0,
      error: connected ? null : payload.error || `HTTP ${response.status ?? 'error'}`,
    }
  } catch (error) {
    return {
      connected: false,
      camera: null,
      frames: 0,
      error: error instanceof Error ? error.message : '카메라 상태 확인 실패',
    }
  }
}
