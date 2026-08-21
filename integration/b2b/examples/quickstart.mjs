/**
 * Node.js 18+ server-side example. No third-party dependency is required.
 *
 * PowerShell:
 *   $env:PROFILE_ENGINE_BASE_URL='http://localhost:8000'
 *   $env:PROFILE_ENGINE_TENANT_ID='test-tenant'
 *   $env:PROFILE_ENGINE_API_KEY='local-development-key'
 *   node integration/b2b/examples/quickstart.mjs
 */

const baseUrl = (process.env.PROFILE_ENGINE_BASE_URL || '').replace(/\/$/, '');
const tenantId = process.env.PROFILE_ENGINE_TENANT_ID;
const apiKey = process.env.PROFILE_ENGINE_API_KEY;
const timeoutMs = Number(process.env.PROFILE_ENGINE_TIMEOUT_MS || 10_000);

if (!baseUrl || !tenantId || !apiKey) {
  throw new Error('Missing PROFILE_ENGINE_BASE_URL, PROFILE_ENGINE_TENANT_ID or PROFILE_ENGINE_API_KEY');
}

class ApiError extends Error {
  constructor(status, body, requestId) {
    super(`Profile Engine HTTP ${status}: ${body?.code || body?.detail || 'unknown_error'}`);
    this.status = status;
    this.body = body;
    this.requestId = requestId;
  }
}

async function call(path, { method = 'GET', body, idempotencyKey } = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(`${baseUrl}${path}`, {
      method,
      headers: {
        'X-API-Key': apiKey,
        'X-Tenant-ID': tenantId,
        ...(idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : {}),
        ...(body ? { 'Content-Type': 'application/json' } : {}),
      },
      body: body ? JSON.stringify(body) : undefined,
      signal: controller.signal,
    });
    const payload = await response.json();
    if (!response.ok) {
      throw new ApiError(response.status, payload, response.headers.get('X-Request-ID'));
    }
    return payload;
  } finally {
    clearTimeout(timeout);
  }
}

async function getOrInitialize(userId) {
  try {
    return await call(`/v1/profiles/${encodeURIComponent(userId)}`);
  } catch (error) {
    if (!(error instanceof ApiError) || error.status !== 404) throw error;
    return call('/v1/profiles:init', {
      method: 'POST',
      idempotencyKey: `init:${userId}`,
      body: {
        tenant_user_id: userId,
        consent: { profile: true, sensitive_inference: false },
      },
    });
  }
}

async function ingestUserMessage({ userId, sessionId, turnId, text }) {
  let current = await getOrInitialize(userId);
  const requestBody = () => ({
    conversation_id: sessionId,
    message_id: turnId,
    expected_profile_version: current.profile_version,
    occurred_at: new Date().toISOString(),
    text,
    context: { previous_turn_count: 0, recent_turns: [] },
  });

  const firstBody = requestBody();
  try {
    return await call(`/v1/profiles/${encodeURIComponent(userId)}/messages:ingest`, {
      method: 'POST',
      idempotencyKey: turnId,
      body: firstBody,
    });
  } catch (error) {
    if (!(error instanceof ApiError) || error.status !== 409) throw error;
    current = await call(`/v1/profiles/${encodeURIComponent(userId)}`);
    return call(`/v1/profiles/${encodeURIComponent(userId)}/messages:ingest`, {
      method: 'POST',
      idempotencyKey: turnId,
      body: { ...firstBody, expected_profile_version: current.profile_version },
    });
  }
}

const suffix = Date.now().toString(36);
const result = await ingestUserMessage({
  userId: `b2b-quickstart-${suffix}`,
  sessionId: `session-${suffix}`,
  turnId: `turn-${suffix}`,
  text: '以后回答短一点，先听我把话说完。',
});

console.log(JSON.stringify({
  profile_version: result.profile_version,
  no_profile_change: result.no_profile_change,
  reply_hints: result.reply_hints,
  request_id: result.request_id,
}, null, 2));
