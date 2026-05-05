(function () {
  'use strict'

  if (typeof window === 'undefined' || typeof window.fetch !== 'function') {
    return
  }

  const nativeFetch = window.fetch.bind(window)
  const QUEUE_STORAGE_KEY = 'azs_offline_queue_v1'
  const CACHE_STORAGE_KEY = 'azs_offline_cache_v1'
  const TRANSIENT_STATUSES = new Set([408, 425, 429])

  let flushInProgress = false

  function nowIso() {
    return new Date().toISOString()
  }

  function isTransientStatus(status) {
    return TRANSIENT_STATUSES.has(status) || status >= 500
  }

  function readJson(key, fallback) {
    try {
      const raw = localStorage.getItem(key)
      return raw ? JSON.parse(raw) : fallback
    } catch {
      return fallback
    }
  }

  function writeJson(key, value) {
    try {
      localStorage.setItem(key, JSON.stringify(value))
    } catch {}
  }

  function readQueue() {
    const queue = readJson(QUEUE_STORAGE_KEY, [])
    return Array.isArray(queue) ? queue : []
  }

  function writeQueue(queue) {
    writeJson(QUEUE_STORAGE_KEY, queue)
  }

  function readCacheStore() {
    const store = readJson(CACHE_STORAGE_KEY, {})
    return store && typeof store === 'object' ? store : {}
  }

  function writeCacheStore(store) {
    writeJson(CACHE_STORAGE_KEY, store)
  }

  function cacheId(scope, key) {
    return `${scope || 'global'}::${key}`
  }

  function queueId() {
    return `q_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`
  }

  function plainHeaders(headersInit) {
    const headers = new Headers(headersInit || {})
    const result = {}
    headers.forEach((value, key) => {
      result[key] = value
    })
    return result
  }

  function buildInitWithoutOfflineFields(init) {
    const next = { ...(init || {}) }
    delete next.offlineAuth
    delete next.offlineCacheKey
    delete next.offlineCacheScope
    delete next.offlineAllowStale
    delete next.offlineQueue
    delete next.offlineForceQueue
    delete next.offlineQueueScope
    delete next.offlineInvalidateCacheKeys
    delete next.offlineReplayLabel
    return next
  }

  function resolveAuth(init, authKind) {
    const next = { ...init }
    const headers = new Headers(init.headers || {})

    if (authKind === 'mobile') {
      const token = localStorage.getItem('azs_token')
      if (token) headers.set('Authorization', `Bearer ${token}`)
    }

    if (authKind === 'cashier') {
      const token = localStorage.getItem('azs_cashier_token')
      if (token) headers.set('Authorization', `Bearer ${token}`)
    }

    if (authKind === 'admin' && next.credentials == null) {
      next.credentials = 'include'
    }

    next.headers = headers
    return next
  }

  function buildQueuedResponse(item) {
    return new Response(JSON.stringify({ queued: true, offline: true, queue_id: item.id }), {
      status: 202,
      headers: {
        'Content-Type': 'application/json',
        'X-AZS-Offline-Queued': '1',
        'X-AZS-Offline-Queue-Id': item.id,
      },
    })
  }

  function buildCachedResponse(entry) {
    return new Response(entry.body, {
      status: 200,
      headers: {
        'Content-Type': entry.contentType || 'application/json',
        'X-AZS-Offline-Cached': '1',
        'X-AZS-Offline-Saved-At': entry.savedAt || '',
      },
    })
  }

  async function storeCacheEntry(cacheKey, cacheScope, response) {
    if (!response || !response.ok) return
    const contentType = response.headers.get('Content-Type') || 'application/json'
    const body = await response.clone().text()
    const store = readCacheStore()
    store[cacheId(cacheScope, cacheKey)] = {
      body,
      contentType,
      savedAt: nowIso(),
    }
    writeCacheStore(store)
  }

  function getCacheEntry(cacheKey, cacheScope) {
    const store = readCacheStore()
    return store[cacheId(cacheScope, cacheKey)] || null
  }

  function invalidateCacheKeys(cacheKeys, cacheScope) {
    if (!Array.isArray(cacheKeys) || !cacheKeys.length) return
    const store = readCacheStore()
    let changed = false
    cacheKeys.forEach((key) => {
      const id = cacheId(cacheScope, key)
      if (Object.prototype.hasOwnProperty.call(store, id)) {
        delete store[id]
        changed = true
      }
    })
    if (changed) writeCacheStore(store)
  }

  function normalizeUrl(input) {
    if (typeof input === 'string') return input
    if (input instanceof URL) return input.toString()
    if (input && typeof input.url === 'string') return input.url
    return String(input || '')
  }

  function upsertQueueItem(item) {
    const queue = readQueue()
    const replaceable = item.method === 'PATCH' || item.method === 'PUT'
    if (replaceable) {
      const existingIndex = queue.findIndex((entry) => (
        entry.method === item.method
        && entry.url === item.url
        && entry.offlineAuth === item.offlineAuth
        && entry.queueScope === item.queueScope
      ))
      if (existingIndex >= 0) {
        queue[existingIndex] = { ...queue[existingIndex], ...item, id: queue[existingIndex].id }
        writeQueue(queue)
        return queue[existingIndex]
      }
    }
    queue.push(item)
    writeQueue(queue)
    return item
  }

  function queueRequest(input, init) {
    const item = upsertQueueItem({
      id: queueId(),
      url: normalizeUrl(input),
      method: String(init.method || 'POST').toUpperCase(),
      body: init.body == null ? null : String(init.body),
      headers: plainHeaders(init.headers),
      credentials: init.credentials || null,
      offlineAuth: init.offlineAuth || 'public',
      queueScope: init.offlineQueueScope || 'global',
      invalidateCacheKeys: Array.isArray(init.offlineInvalidateCacheKeys) ? init.offlineInvalidateCacheKeys : [],
      replayLabel: init.offlineReplayLabel || '',
      createdAt: nowIso(),
    })

    window.dispatchEvent(new CustomEvent('azs:offline-queued', { detail: { item, queueSize: readQueue().length } }))
    return buildQueuedResponse(item)
  }

  async function fetchWithCache(input, init) {
    const cacheKey = init.offlineCacheKey
    const cacheScope = init.offlineCacheScope || 'global'
    const allowStale = init.offlineAllowStale !== false
    const stripped = buildInitWithoutOfflineFields(init)
    const requestInit = resolveAuth(stripped, init.offlineAuth)
    const cached = cacheKey ? getCacheEntry(cacheKey, cacheScope) : null

    try {
      const response = await nativeFetch(input, requestInit)
      if (response.ok && cacheKey) {
        await storeCacheEntry(cacheKey, cacheScope, response)
      }
      if (!response.ok && allowStale && cached && isTransientStatus(response.status)) {
        return buildCachedResponse(cached)
      }
      return response
    } catch (error) {
      if (allowStale && cached) {
        return buildCachedResponse(cached)
      }
      throw error
    }
  }

  async function fetchWithQueue(input, init) {
    const stripped = buildInitWithoutOfflineFields(init)
    const requestInit = resolveAuth(stripped, init.offlineAuth)
    const queuedInit = {
      ...requestInit,
      offlineAuth: init.offlineAuth,
      offlineQueueScope: init.offlineQueueScope,
      offlineInvalidateCacheKeys: init.offlineInvalidateCacheKeys,
      offlineReplayLabel: init.offlineReplayLabel,
    }

    if (init.offlineForceQueue || (!navigator.onLine && init.offlineQueue)) {
      return queueRequest(input, queuedInit)
    }

    try {
      const response = await nativeFetch(input, requestInit)
      if (response.ok) {
        invalidateCacheKeys(init.offlineInvalidateCacheKeys, init.offlineQueueScope || 'global')
      }
      return response
    } catch (error) {
      if (init.offlineQueue) {
        return queueRequest(input, queuedInit)
      }
      throw error
    }
  }

  async function flushQueue() {
    if (flushInProgress) return { flushed: 0, pending: readQueue().length }
    if (!navigator.onLine) return { flushed: 0, pending: readQueue().length }

    const queue = readQueue()
    if (!queue.length) return { flushed: 0, pending: 0 }

    flushInProgress = true
    let flushed = 0
    const pending = []

    try {
      for (let index = 0; index < queue.length; index += 1) {
        const item = queue[index]
        const init = resolveAuth({
          method: item.method,
          headers: item.headers,
          body: item.body,
          credentials: item.credentials || undefined,
        }, item.offlineAuth)

        try {
          const response = await nativeFetch(item.url, init)
          if (response.ok) {
            invalidateCacheKeys(item.invalidateCacheKeys, item.queueScope)
            flushed += 1
            window.dispatchEvent(new CustomEvent('azs:offline-queue-item-sent', { detail: { item } }))
            continue
          }

          if (response.status === 401 || response.status === 403 || isTransientStatus(response.status)) {
            pending.push(item)
            pending.push(...queue.slice(index + 1))
            break
          }

          window.dispatchEvent(new CustomEvent('azs:offline-queue-item-dropped', { detail: { item, status: response.status } }))
        } catch {
          pending.push(item)
          pending.push(...queue.slice(index + 1))
          break
        }
      }

      writeQueue(pending)
    } finally {
      flushInProgress = false
    }

    if (flushed > 0) {
      window.dispatchEvent(new CustomEvent('azs:offline-queue-flushed', {
        detail: { flushed, pending: pending.length },
      }))
    }

    return { flushed, pending: pending.length }
  }

  window.fetch = async function patchedFetch(input, init = {}) {
    const method = String(init.method || 'GET').toUpperCase()
    if (method === 'GET' && init.offlineCacheKey) {
      return fetchWithCache(input, init)
    }
    if (method !== 'GET' && init.offlineQueue) {
      return fetchWithQueue(input, init)
    }
    return nativeFetch(input, buildInitWithoutOfflineFields(resolveAuth(init, init.offlineAuth)))
  }

  window.addEventListener('online', () => {
    flushQueue()
  })

  window.setTimeout(() => {
    if (navigator.onLine) flushQueue()
  }, 1500)

  window.AZSOffline = {
    flushQueue,
    getQueueSize: () => readQueue().length,
    isQueuedResponse: (response) => response?.headers?.get('X-AZS-Offline-Queued') === '1',
    isCachedResponse: (response) => response?.headers?.get('X-AZS-Offline-Cached') === '1',
    getCachedSavedAt: (response) => response?.headers?.get('X-AZS-Offline-Saved-At') || null,
    clearQueue: () => writeQueue([]),
  }
})()