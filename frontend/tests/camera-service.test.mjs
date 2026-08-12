import assert from 'node:assert/strict'
import test from 'node:test'

import { cameraStreamUrl, readCameraHealth } from '../src/services/camera.mjs'

test('camera stream URL uses configured Vite URL', () => {
  assert.equal(
    cameraStreamUrl({ VITE_CAMERA_STREAM_URL: 'http://robot.test/live' }),
    'http://robot.test/live',
  )
})

test('camera stream URL defaults to the Raspberry Pi MJPEG endpoint', () => {
  assert.equal(cameraStreamUrl({}), 'http://192.168.100.246:8081/stream.mjpg')
})

test('camera health reports connected only when HTTP and payload are healthy', async () => {
  const result = await readCameraHealth(async () => ({
    ok: true,
    json: async () => ({ ok: true, camera: 'Brio 100', frames: 42, error: null }),
  }))

  assert.deepEqual(result, {
    connected: true,
    camera: 'Brio 100',
    frames: 42,
    error: null,
  })
})

test('camera health turns network failures into an offline state', async () => {
  const result = await readCameraHealth(async () => {
    throw new Error('network down')
  })

  assert.equal(result.connected, false)
  assert.equal(result.camera, null)
  assert.match(result.error, /network down/)
})
