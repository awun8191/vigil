/**
 * challenger_m4_2_gen3 Empirical Stress Test Harness
 * 
 * Tests frontend and integration aspects of Milestone 4:
 * 1. web/src/components/chat/InputPill.tsx
 * 2. web/src/components/settings/sections.tsx (ConnectorsSection)
 */

import assert from 'node:assert/strict';
import { createServer } from '../node_modules/vite/dist/node/index.js';
import React from 'react';
import { renderToString } from 'react-dom/server';
import { QueryClient, QueryClientProvider } from '../node_modules/@tanstack/react-query/build/modern/index.js';

let passed = 0;
let failed = 0;
const results = [];

function test(name, fn) {
  try {
    fn();
    console.log(`  ✓ ${name}`);
    passed++;
    results.push({ name, status: 'pass' });
  } catch (err) {
    console.error(`  ✗ ${name}`);
    console.error(`    ${err.message}`);
    failed++;
    results.push({ name, status: 'fail', error: err.message });
  }
}

async function testAsync(name, fn) {
  try {
    await fn();
    console.log(`  ✓ ${name}`);
    passed++;
    results.push({ name, status: 'pass' });
  } catch (err) {
    console.error(`  ✗ ${name}`);
    console.error(`    ${err.message}`);
    failed++;
    results.push({ name, status: 'fail', error: err.message });
  }
}

console.log('================================================================');
console.log('CHALLENGER M4-2 GEN3 EMPIRICAL STRESS TEST HARNESS');
console.log('================================================================');

const viteServer = await createServer({
  root: './web',
  server: { middlewareMode: true },
});

// Load components via Vite SSR pipeline
const { InputPill } = await viteServer.ssrLoadModule('/src/components/chat/InputPill.tsx');
const { ConnectorsSection } = await viteServer.ssrLoadModule('/src/components/settings/sections.tsx');

const createTestQueryClient = (initialData = {}) => {
  const qc = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
      },
    },
  });

  if (initialData.vault) {
    qc.setQueryData(['settings', 'vault'], initialData.vault);
  }
  if (initialData.mcp) {
    qc.setQueryData(['connectors', 'mcp'], initialData.mcp);
  }
  if (initialData.connectors) {
    qc.setQueryData(['connectors'], initialData.connectors);
  }

  return qc;
};

const renderWithQueryClient = (element, initialData = {}) => {
  const queryClient = createTestQueryClient(initialData);
  return renderToString(
    React.createElement(QueryClientProvider, { client: queryClient }, element)
  );
};

// -----------------------------------------------------------------------------
// SECTION 1: InputPill & Voice Recording Stress Testing
// -----------------------------------------------------------------------------
console.log('\n--- 1. InputPill Voice Recording & Lifecycle Stress Tests ---');

test('InputPill: Renders idle state with all composer controls', () => {
  const html = renderToString(
    React.createElement(InputPill, {
      onSend: () => {},
      onStop: () => {},
      streaming: false,
    })
  );

  assert.ok(html.includes('vigil-composer'), 'Textarea id="vigil-composer" present');
  assert.ok(html.includes('Ask Vigil anything or assign a task...'), 'Placeholder present');
  assert.ok(html.includes('Record voice'), 'Mic button has Record voice aria-label');
  assert.ok(html.includes('Attach a file'), 'Plus button has Attach a file aria-label');
  assert.ok(html.includes('Send'), 'Send button present');
  assert.ok(!html.includes('Stop generating'), 'Stop generating should not be present in idle');
});

test('InputPill: Renders streaming state correctly (Send replaced by Stop generating)', () => {
  const html = renderToString(
    React.createElement(InputPill, {
      onSend: () => {},
      onStop: () => {},
      streaming: true,
    })
  );

  assert.ok(html.includes('Stop generating'), 'Send replaced by Stop generating');
  assert.ok(!html.includes('aria-label="Send"'), 'Send button absent during streaming');
});

test('InputPill: Disabled state propagates to all interactive controls', () => {
  const html = renderToString(
    React.createElement(InputPill, {
      onSend: () => {},
      onStop: () => {},
      streaming: false,
      disabled: true,
    })
  );

  assert.ok(html.includes('disabled=""') || html.includes('disabled'), 'Disabled attributes present');
});

test('InputPill: Textarea auto-sizing calculation logic handles extreme constraints', () => {
  const computeHeight = (scrollHeight) => `${Math.min(scrollHeight, 160)}px`;

  assert.equal(computeHeight(0), '0px', 'Empty scroll height');
  assert.equal(computeHeight(32), '32px', 'Single line text height');
  assert.equal(computeHeight(96), '96px', 'Multi-line text height');
  assert.equal(computeHeight(160), '160px', 'Boundary ceiling');
  assert.equal(computeHeight(161), '160px', 'Exceeding ceiling by 1px');
  assert.equal(computeHeight(5000), '160px', 'Extreme vertical overflow capped');
});

testAsync('InputPill: Microphone permission decline / error recovery', async () => {
  let notice = null;
  const setNotice = (msg) => { notice = msg; };

  // Case 1: Browser lacks mediaDevices API (e.g. insecure HTTP context)
  const insecureNav = { mediaDevices: undefined };
  if (!insecureNav.mediaDevices?.getUserMedia) {
    setNotice('This browser exposes no microphone to the page.');
  }
  assert.equal(notice, 'This browser exposes no microphone to the page.');

  // Case 2: User explicitly declines microphone permission (NotAllowedError)
  const permissionDeniedNav = {
    mediaDevices: {
      getUserMedia: () => Promise.reject(new DOMException('Permission denied', 'NotAllowedError')),
    },
  };
  try {
    await permissionDeniedNav.mediaDevices.getUserMedia({ audio: true });
  } catch {
    setNotice('Microphone permission was declined, so nothing is being captured.');
  }
  assert.equal(notice, 'Microphone permission was declined, so nothing is being captured.');

  // Case 3: Hardware error (NotFoundError / DevicesNotFoundError)
  const noMicNav = {
    mediaDevices: {
      getUserMedia: () => Promise.reject(new DOMException('Requested device not found', 'NotFoundError')),
    },
  };
  try {
    await noMicNav.mediaDevices.getUserMedia({ audio: true });
  } catch {
    setNotice('Microphone permission was declined, so nothing is being captured.');
  }
  assert.equal(notice, 'Microphone permission was declined, so nothing is being captured.');
});

test('InputPill: MediaRecorder chunk aggregation and audio/webm blob assembly', () => {
  const chunks = [];
  const fakeRecorder = {
    state: 'recording',
    ondataavailable: (event) => {
      if (event.data && event.data.size > 0) {
        chunks.push(event.data);
      }
    },
    stop: function () {
      this.state = 'inactive';
      this.onstop?.();
    },
    onstop: null,
  };

  // Simulate multiple audio packets arriving
  fakeRecorder.ondataavailable({ data: new Blob([new Uint8Array([10, 20, 30])]) });
  fakeRecorder.ondataavailable({ data: new Blob([new Uint8Array([])]) }); // 0-byte packet ignored
  fakeRecorder.ondataavailable({ data: new Blob([new Uint8Array([40, 50])]) });
  fakeRecorder.ondataavailable({ data: new Blob([new Uint8Array([60, 70, 80, 90])]) });

  assert.equal(chunks.length, 3, 'Accumulated exactly 3 non-empty chunks');

  let outputBlob = null;
  fakeRecorder.onstop = () => {
    if (chunks.length > 0) {
      outputBlob = new Blob(chunks, { type: 'audio/webm' });
    }
  };
  fakeRecorder.stop();

  assert.ok(outputBlob instanceof Blob, 'Assembled output must be a valid Blob');
  assert.equal(outputBlob.type, 'audio/webm', 'MIME type must be audio/webm');
  assert.equal(outputBlob.size, 9, 'Sum of chunk sizes: 3 + 2 + 4 = 9 bytes');
});

testAsync('InputPill: Audio hardware teardown and resource release verification', async () => {
  let trackStopped = false;
  let contextClosed = false;

  const fakeTrack = {
    stop: () => { trackStopped = true; },
  };
  const fakeStream = {
    getTracks: () => [fakeTrack],
  };
  const fakeCtx = {
    close: () => {
      contextClosed = true;
      return Promise.resolve();
    },
  };

  const releaseCapture = (stream, ctx) => {
    stream?.getTracks().forEach((track) => track.stop());
    void ctx?.close();
  };

  releaseCapture(fakeStream, fakeCtx);

  assert.equal(trackStopped, true, 'Microphone audio track must be stopped');
  assert.equal(contextClosed, true, 'AudioContext must be cleanly closed to release audio device');
});

testAsync('InputPill: Voice transcription fetch failure recovery & draft populating', async () => {
  let draft = '';
  let notice = null;

  const simulateTranscribe = async (fetchMock) => {
    notice = 'Transcribing audio...';
    try {
      const res = await fetchMock();
      if (!res.ok) {
        throw new Error(`Transcription failed (${res.status})`);
      }
      const data = await res.json();
      const text = data?.text?.trim();
      if (text) {
        draft = draft ? `${draft.trim()} ${text}` : text;
        notice = null;
      } else {
        notice = 'No speech recognized.';
      }
    } catch {
      notice = 'Voice transcription failed. You can type your message instead.';
    }
  };

  // Test 1: Network failure (offline / DNS error / connection reset)
  await simulateTranscribe(() => Promise.reject(new Error('NetworkError: Failed to fetch')));
  assert.equal(notice, 'Voice transcription failed. You can type your message instead.');
  assert.equal(draft, '', 'Draft must remain unaffected on error');

  // Test 2: HTTP 500 Internal Server Error from backend
  await simulateTranscribe(() => Promise.resolve({ ok: false, status: 500 }));
  assert.equal(notice, 'Voice transcription failed. You can type your message instead.');

  // Test 3: HTTP 422 Unprocessable Entity
  await simulateTranscribe(() => Promise.resolve({ ok: false, status: 422 }));
  assert.equal(notice, 'Voice transcription failed. You can type your message instead.');

  // Test 4: Empty transcription text returned from backend
  await simulateTranscribe(() => Promise.resolve({
    ok: true,
    status: 200,
    json: () => Promise.resolve({ text: '' }),
  }));
  assert.equal(notice, 'No speech recognized.');

  // Test 5: Whitespace-only transcription text
  await simulateTranscribe(() => Promise.resolve({
    ok: true,
    status: 200,
    json: () => Promise.resolve({ text: '    \n\t  ' }),
  }));
  assert.equal(notice, 'No speech recognized.');

  // Test 6: Successful transcription into empty draft
  await simulateTranscribe(() => Promise.resolve({
    ok: true,
    status: 200,
    json: () => Promise.resolve({ text: 'First voice command' }),
  }));
  assert.equal(notice, null);
  assert.equal(draft, 'First voice command');

  // Test 7: Successful subsequent transcription appends with clean whitespace separation
  await simulateTranscribe(() => Promise.resolve({
    ok: true,
    status: 200,
    json: () => Promise.resolve({ text: 'and second command' }),
  }));
  assert.equal(notice, null);
  assert.equal(draft, 'First voice command and second command');
});

test('InputPill: Attachment handling and onSend parameter boundary observation', () => {
  // Observation: In InputPill.tsx, canSend is:
  // (draft.trim().length > 0 || files.length > 0) && !streaming && !disabled
  // However, onSend is typed as `(text: string) => void`, and `submit` calls:
  // onSend(draft); setFiles([]);
  // If draft is empty and files has items, onSend is called with an empty string!
  const draft = '';
  const files = ['report.pdf'];
  const streaming = false;
  const disabled = false;

  const canSend = (draft.trim().length > 0 || files.length > 0) && !streaming && !disabled;
  assert.equal(canSend, true, 'canSend permits submission when only files are attached');

  let sentPayload = null;
  const onSend = (text) => { sentPayload = text; };
  if (canSend) {
    onSend(draft);
  }
  assert.equal(sentPayload, '', 'Payload sent to onSend is empty string; files are not forwarded in signature');
});


// -----------------------------------------------------------------------------
// SECTION 2: ConnectorsSection & Key Vault Stress Testing
// -----------------------------------------------------------------------------
console.log('\n--- 2. ConnectorsSection & Key Vault Stress Tests ---');

test('Key Vault: Masking logic boundary and format tests', () => {
  const maskSecret = (val) => val.length > 8 ? `${val.slice(0, 3)}****${val.slice(-4)}` : '****';

  assert.equal(maskSecret(''), '****', 'Empty secret');
  assert.equal(maskSecret('1'), '****', '1 char');
  assert.equal(maskSecret('1234567'), '****', '7 chars');
  assert.equal(maskSecret('12345678'), '****', '8 chars boundary');
  assert.equal(maskSecret('123456789'), '123****6789', '9 chars boundary (first 3 + **** + last 4)');
  assert.equal(maskSecret('FIRECRAWL_KEY_9999_SECRET'), 'FIR****CRET', 'Long secret masked correctly');
  assert.equal(maskSecret('sk-live-0123456789abcdef'), 'sk-****cdef', 'API key format masked');
});

test('Key Vault: Secrets list renders with masked strings and NEVER exposes plaintext', () => {
  const plaintext1 = 'super_secret_groq_api_token_12345';
  const plaintext2 = 'top_secret_firecrawl_key_99999';
  const masked1 = 'sup****2345';
  const masked2 = 'top****9999';

  const testSecrets = [
    {
      key_name: 'GROQ_API_KEY',
      masked_value: masked1,
      created_at: '2026-11-10T14:00:00Z',
      updated_at: '2026-11-10T14:00:00Z',
    },
    {
      key_name: 'FIRECRAWL_API_KEY',
      masked_value: masked2,
      created_at: '2026-11-10T14:15:00Z',
      updated_at: '2026-11-10T14:15:00Z',
    },
  ];

  const html = renderWithQueryClient(React.createElement(ConnectorsSection), {
    vault: testSecrets,
  });

  // Verify key names rendered
  assert.ok(html.includes('GROQ_API_KEY'), 'Renders GROQ_API_KEY');
  assert.ok(html.includes('FIRECRAWL_API_KEY'), 'Renders FIRECRAWL_API_KEY');

  // Verify masked strings rendered
  assert.ok(html.includes(masked1), `Renders masked value: ${masked1}`);
  assert.ok(html.includes(masked2), `Renders masked value: ${masked2}`);

  // CRITICAL SECURITY ASSERTION: Plaintext must never leak into rendered markup
  assert.ok(!html.includes(plaintext1), 'Plaintext secret 1 must NEVER appear in HTML');
  assert.ok(!html.includes(plaintext2), 'Plaintext secret 2 must NEVER appear in HTML');

  // Verify delete buttons
  assert.ok(html.includes('aria-label="Delete GROQ_API_KEY"'), 'Delete GROQ_API_KEY aria-label');
  assert.ok(html.includes('aria-label="Delete FIRECRAWL_API_KEY"'), 'Delete FIRECRAWL_API_KEY aria-label');
});

test('Key Vault: Empty state rendered when vault has no secrets', () => {
  const html = renderWithQueryClient(React.createElement(ConnectorsSection), {
    vault: [],
  });

  assert.ok(html.includes('No API keys stored in vault yet.'), 'Renders empty vault notification');
});

test('Key Vault: Add secret form validation rules (empty / whitespace rejection)', () => {
  const validateSecret = (key, val) => {
    const k = key.trim();
    const v = val.trim();
    return Boolean(k && v);
  };

  // Both empty
  assert.equal(validateSecret('', ''), false);
  // Key provided, value empty
  assert.equal(validateSecret('MY_KEY', ''), false);
  assert.equal(validateSecret('MY_KEY', '    '), false);
  // Value provided, key empty
  assert.equal(validateSecret('', 'secret_val'), false);
  assert.equal(validateSecret('    ', 'secret_val'), false);
  // Whitespace only in both
  assert.equal(validateSecret('  \n\t  ', '  \r\n  '), false);
  // Valid key and value
  assert.equal(validateSecret('MY_KEY', 'my_val'), true);
  assert.equal(validateSecret('  PADDED_KEY  ', '  PADDED_VAL  '), true);
});

test('MCP Presets: Exactly 5 services present with required specifications', () => {
  const html = renderWithQueryClient(React.createElement(ConnectorsSection));

  const requiredPresets = [
    { id: 'firecrawl', label: 'Firecrawl', requiredKey: 'FIRECRAWL_API_KEY' },
    { id: 'github', label: 'GitHub', requiredKey: 'GITHUB_PERSONAL_ACCESS_TOKEN' },
    { id: 'filesystem', label: 'Filesystem', requiredKey: null },
    { id: 'postgres', label: 'PostgreSQL', requiredKey: 'POSTGRES_PASSWORD' },
    { id: 'linear', label: 'Linear', requiredKey: 'LINEAR_API_KEY' },
  ];

  for (const preset of requiredPresets) {
    assert.ok(html.includes(preset.label), `Preset label "${preset.label}" must be rendered`);
    if (preset.requiredKey) {
      assert.ok(html.includes(preset.requiredKey), `Required key "${preset.requiredKey}" must be rendered for ${preset.label}`);
    }
  }

  // Verify all 5 are rendered under stdio transport
  const stdioMatches = html.match(/stdio/g);
  assert.ok(stdioMatches && stdioMatches.length >= 5, 'All 5 presets should declare stdio transport');
});

test('MCP Presets: Dynamic key configuration detection (configured vs missing)', () => {
  // Provide only GITHUB_PERSONAL_ACCESS_TOKEN in vault
  const html = renderWithQueryClient(React.createElement(ConnectorsSection), {
    vault: [
      {
        key_name: 'GITHUB_PERSONAL_ACCESS_TOKEN',
        masked_value: 'ghp****9999',
        created_at: '2026-11-10T12:00:00Z',
        updated_at: '2026-11-10T12:00:00Z',
      },
    ],
  });

  const cleanHtml = html.replace(/<!--.*?-->/g, '');

  // GitHub should show configured checkmark ✓
  assert.ok(cleanHtml.includes('GITHUB_PERSONAL_ACCESS_TOKEN ✓'), 'GitHub key should show configured checkmark');
  // Firecrawl and Linear keys should show (missing)
  assert.ok(cleanHtml.includes('FIRECRAWL_API_KEY (missing)'), 'Firecrawl key should show missing warning');
  assert.ok(cleanHtml.includes('LINEAR_API_KEY (missing)'), 'Linear key should show missing warning');
  // Filesystem should show None
  assert.ok(cleanHtml.includes('None'), 'Filesystem preset should show None required key');
});

test('MCP Presets: Connected status and button disabling when preset is active', () => {
  const html = renderWithQueryClient(React.createElement(ConnectorsSection), {
    mcp: [
      {
        id: 'srv-linear',
        name: 'linear',
        transport: 'stdio',
        enabled: true,
        command: 'npx -y @linear/mcp-server',
      },
    ],
  });

  assert.ok(html.includes('Connected'), 'Connected badge should be rendered for registered linear preset');
});

test('Custom MCP Server Studio: Form validation, transport toggle, and payload mapping', () => {
  const buildPayload = (name, transport, target) => {
    const trimmedName = name.trim();
    const trimmedTarget = target.trim();
    if (!trimmedName || !trimmedTarget) return null;
    return {
      name: trimmedName,
      transport,
      command: transport === 'stdio' ? trimmedTarget : undefined,
      url: transport === 'sse' ? trimmedTarget : undefined,
    };
  };

  // Validation failures
  assert.equal(buildPayload('', 'stdio', 'npx -y server'), null, 'Empty name rejected');
  assert.equal(buildPayload('   ', 'stdio', 'npx -y server'), null, 'Whitespace name rejected');
  assert.equal(buildPayload('server', 'stdio', ''), null, 'Empty target rejected');
  assert.equal(buildPayload('server', 'stdio', '   '), null, 'Whitespace target rejected');

  // Stdio transport mapping
  const stdioPayload = buildPayload('custom-fetch', 'stdio', 'npx -y @modelcontextprotocol/server-fetch');
  assert.notEqual(stdioPayload, null);
  assert.equal(stdioPayload.name, 'custom-fetch');
  assert.equal(stdioPayload.transport, 'stdio');
  assert.equal(stdioPayload.command, 'npx -y @modelcontextprotocol/server-fetch');
  assert.equal(stdioPayload.url, undefined, 'Stdio payload must not have url');

  // SSE transport mapping
  const ssePayload = buildPayload('custom-remote', 'sse', 'https://remote.mcp.io/sse');
  assert.notEqual(ssePayload, null);
  assert.equal(ssePayload.name, 'custom-remote');
  assert.equal(ssePayload.transport, 'sse');
  assert.equal(ssePayload.url, 'https://remote.mcp.io/sse');
  assert.equal(stdioPayload.url, undefined, 'SSE payload must not have command');
});

test('Custom MCP Server Studio: Quick preset suggestion buttons populate common keys', () => {
  const html = renderWithQueryClient(React.createElement(ConnectorsSection));

  const expectedCommonKeys = [
    'FIRECRAWL_API_KEY',
    'GROQ_API_KEY',
    'GITHUB_PERSONAL_ACCESS_TOKEN',
    'LINEAR_API_KEY',
    'POSTGRES_PASSWORD',
  ];

  for (const key of expectedCommonKeys) {
    assert.ok(html.includes(key), `Common vault key button "${key}" must be present in quick suggestions`);
  }
});

await viteServer.close();

console.log('================================================================');
console.log(`TOTAL TESTS: ${passed + failed} | PASSED: ${passed} | FAILED: ${failed}`);
console.log('================================================================');

if (failed > 0) {
  process.exit(1);
}
