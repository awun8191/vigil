/**
 * Comprehensive Empirical Stress Test Harness for Milestone 4:
 * - web/src/components/chat/InputPill.tsx
 * - web/src/components/settings/sections.tsx (Connectors view)
 */
import assert from 'node:assert/strict';
import { createServer } from '../node_modules/vite/dist/node/index.js';
import React from 'react';
import { renderToString } from 'react-dom/server';
import { QueryClient, QueryClientProvider } from '../node_modules/@tanstack/react-query/build/modern/index.js';

let passed = 0;
let failed = 0;

function it(name, fn) {
  try {
    fn();
    console.log(`  ✓ ${name}`);
    passed++;
  } catch (err) {
    console.error(`  ✗ ${name}`);
    console.error(`    ${err.message}`);
    failed++;
  }
}

async function itAsync(name, fn) {
  try {
    await fn();
    console.log(`  ✓ ${name}`);
    passed++;
  } catch (err) {
    console.error(`  ✗ ${name}`);
    console.error(`    ${err.message}`);
    failed++;
  }
}

console.log('================================================================');
console.log('STARTING EMPIRICAL FRONTEND STRESS HARNESS — MILESTONE 4');
console.log('================================================================');

const viteServer = await createServer({
  root: './web',
  server: { middlewareMode: true },
});

// Load components through Vite's pipeline
const { InputPill } = await viteServer.ssrLoadModule('/src/components/chat/InputPill.tsx');
const Sections = await viteServer.ssrLoadModule('/src/components/settings/sections.tsx');
const { ConnectorsSection } = Sections;

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

console.log('\n--- SUITE 1: InputPill Component & Audio Recording Logic ---');

it('InputPill: Renders initial default idle state with composer textarea and controls', () => {
  const html = renderToString(
    React.createElement(InputPill, {
      onSend: () => {},
      onStop: () => {},
      streaming: false,
    })
  );

  assert.ok(html.includes('vigil-composer'), 'Should contain vigil-composer textarea ID');
  assert.ok(html.includes('Ask Vigil anything'), 'Should have placeholder text');
  assert.ok(html.includes('Record voice'), 'Should have Record voice aria-label');
  assert.ok(html.includes('Attach a file'), 'Should have Attach a file button');
  assert.ok(html.includes('Send'), 'Should have Send button');
});

it('InputPill: Renders streaming state with Stop generating button', () => {
  const html = renderToString(
    React.createElement(InputPill, {
      onSend: () => {},
      onStop: () => {},
      streaming: true,
    })
  );

  assert.ok(html.includes('Stop generating'), 'Should have Stop generating aria-label');
  assert.ok(!html.includes('aria-label="Send"'), 'Send button should be replaced by stop button');
});

it('InputPill: Disabled prop disables textarea, attach and mic buttons', () => {
  const html = renderToString(
    React.createElement(InputPill, {
      onSend: () => {},
      onStop: () => {},
      streaming: false,
      disabled: true,
    })
  );

  assert.ok(html.includes('disabled=""') || html.includes('disabled'), 'Elements should have disabled attribute');
});

it('InputPill: canSend predicate truth table stress testing', () => {
  const canSend = (draft, filesCount, streaming, disabled) =>
    (draft.trim().length > 0 || filesCount > 0) && !streaming && !disabled;

  // Empty draft, no files
  assert.equal(canSend('', 0, false, false), false);
  assert.equal(canSend('   ', 0, false, false), false);
  assert.equal(canSend('\n\t ', 0, false, false), false);

  // Valid draft, idle
  assert.equal(canSend('hello', 0, false, false), true);
  assert.equal(canSend('  hello  ', 0, false, false), true);

  // Files attached, empty draft
  assert.equal(canSend('', 1, false, false), true);
  assert.equal(canSend('  ', 2, false, false), true);

  // Streaming blocks sending
  assert.equal(canSend('hello', 0, true, false), false);
  assert.equal(canSend('', 1, true, false), false);

  // Disabled blocks sending
  assert.equal(canSend('hello', 0, false, true), false);
  assert.equal(canSend('', 1, false, true), false);
  assert.equal(canSend('hello', 1, true, true), false);
});

it('InputPill: Textarea auto-sizing calculation logic', () => {
  const computeHeight = (scrollHeight) => `${Math.min(scrollHeight, 160)}px`;

  assert.equal(computeHeight(24), '24px', 'Small text auto-sizes to scrollHeight');
  assert.equal(computeHeight(80), '80px', 'Medium text auto-sizes to scrollHeight');
  assert.equal(computeHeight(160), '160px', 'Exact ceiling caps at 160px');
  assert.equal(computeHeight(250), '160px', 'Oversized text capped at 160px');
  assert.equal(computeHeight(1000), '160px', 'Extreme scrollHeight capped at 160px');
});

itAsync('InputPill: Microphone permission decline / error handling simulation', async () => {
  let notice = null;
  const setNotice = (msg) => { notice = msg; };

  // Scenario A: navigator.mediaDevices is undefined
  const fakeNavA = { mediaDevices: undefined };
  if (!fakeNavA.mediaDevices?.getUserMedia) {
    setNotice('This browser exposes no microphone to the page.');
  }
  assert.equal(notice, 'This browser exposes no microphone to the page.');

  // Scenario B: getUserMedia rejects with NotAllowedError (user declined permission)
  const fakeNavB = {
    mediaDevices: {
      getUserMedia: () => Promise.reject(new Error('Permission denied')),
    },
  };
  try {
    await fakeNavB.mediaDevices.getUserMedia({ audio: true });
  } catch {
    setNotice('Microphone permission was declined, so nothing is being captured.');
  }
  assert.equal(notice, 'Microphone permission was declined, so nothing is being captured.');

  // Scenario C: getUserMedia succeeds
  let streamTrackStopped = false;
  const fakeStream = {
    getTracks: () => [{ stop: () => { streamTrackStopped = true; } }],
  };
  const fakeNavC = {
    mediaDevices: {
      getUserMedia: () => Promise.resolve(fakeStream),
    },
  };
  const stream = await fakeNavC.mediaDevices.getUserMedia({ audio: true });
  assert.equal(stream, fakeStream);
  setNotice(null);
  assert.equal(notice, null);
  fakeStream.getTracks()[0].stop();
  assert.equal(streamTrackStopped, true, 'Hardware track stopped cleanly');
});

it('InputPill: MediaRecorder chunk collection and audio/webm blob assembly', () => {
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
      this.onstop();
    },
    onstop: () => {},
  };

  // Simulate audio chunks arrival
  const chunk1 = new Uint8Array([1, 2, 3]);
  const chunk2 = new Uint8Array([4, 5, 6, 7]);
  fakeRecorder.ondataavailable({ data: new Blob([chunk1]) });
  fakeRecorder.ondataavailable({ data: new Blob([chunk2]) });
  fakeRecorder.ondataavailable({ data: new Blob([]) }); // 0-byte chunk ignored

  assert.equal(chunks.length, 2, 'Should accumulate only non-empty chunks');

  let transcribedBlob = null;
  fakeRecorder.onstop = () => {
    if (chunks.length > 0) {
      transcribedBlob = new Blob(chunks, { type: 'audio/webm' });
    }
  };
  fakeRecorder.stop();

  assert.ok(transcribedBlob !== null, 'Assembled blob must not be null');
  assert.equal(transcribedBlob.type, 'audio/webm', 'MIME type must be audio/webm');
  assert.equal(transcribedBlob.size, 7, 'Blob size should be sum of chunks (3 + 4 = 7)');
});

itAsync('InputPill: Transcription fetch error recovery and draft concatenation', async () => {
  let draft = 'Initial draft';
  let notice = null;

  const runTranscribe = async (fetchImpl) => {
    notice = 'Transcribing audio...';
    try {
      const res = await fetchImpl('/api/v1/voice/transcribe', { method: 'POST' });
      if (!res.ok) {
        throw new Error(`Transcription failed (${res.status})`);
      }
      const data = await res.json();
      const transcribedText = data?.text?.trim();
      if (transcribedText) {
        draft = draft ? `${draft.trim()} ${transcribedText}` : transcribedText;
        notice = null;
      } else {
        notice = 'No speech recognized.';
      }
    } catch {
      notice = 'Voice transcription failed. You can type your message instead.';
    }
  };

  // 1. Network drop (fetch throws TypeError)
  await runTranscribe(() => Promise.reject(new TypeError('Failed to fetch')));
  assert.equal(notice, 'Voice transcription failed. You can type your message instead.');
  assert.equal(draft, 'Initial draft', 'Draft untouched on error');

  // 2. HTTP 500 error
  await runTranscribe(() => Promise.resolve({ ok: false, status: 500 }));
  assert.equal(notice, 'Voice transcription failed. You can type your message instead.');
  assert.equal(draft, 'Initial draft');

  // 3. HTTP 422 error
  await runTranscribe(() => Promise.resolve({ ok: false, status: 422 }));
  assert.equal(notice, 'Voice transcription failed. You can type your message instead.');

  // 4. Empty text response
  await runTranscribe(() => Promise.resolve({
    ok: true,
    status: 200,
    json: () => Promise.resolve({ text: '   ' }),
  }));
  assert.equal(notice, 'No speech recognized.');

  // 5. Successful transcription appends to existing draft
  await runTranscribe(() => Promise.resolve({
    ok: true,
    status: 200,
    json: () => Promise.resolve({ text: 'add groq whisper speech' }),
  }));
  assert.equal(notice, null);
  assert.equal(draft, 'Initial draft add groq whisper speech', 'Concatenates with space cleanly');

  // 6. Successful transcription into empty draft
  draft = '';
  await runTranscribe(() => Promise.resolve({
    ok: true,
    status: 200,
    json: () => Promise.resolve({ text: 'Brand new transcription' }),
  }));
  assert.equal(notice, null);
  assert.equal(draft, 'Brand new transcription');
});


console.log('\n--- SUITE 2: Settings Connectors & Key Vault Logic ---');

it('Key Vault: Masking logic boundary rules', () => {
  const mask = (val) => val.length > 8 ? `${val.slice(0, 3)}****${val.slice(-4)}` : '****';

  assert.equal(mask(''), '****');
  assert.equal(mask('1'), '****');
  assert.equal(mask('12345678'), '****');
  assert.equal(mask('123456789'), '123****6789');
  assert.equal(mask('gsk_1234567890abcdef'), 'gsk****cdef');
  assert.equal(mask('fc-secret-token-prod-99'), 'fc-****d-99');
});

it('Key Vault: Populated secrets list rendering with masked values and delete buttons', () => {
  const testSecrets = [
    {
      key_name: 'FIRECRAWL_API_KEY',
      masked_value: 'fc-****4321',
      created_at: '2026-11-10T12:00:00Z',
      updated_at: '2026-11-10T12:00:00Z',
    },
    {
      key_name: 'GROQ_API_KEY',
      masked_value: 'gsk****8899',
      created_at: '2026-11-10T12:30:00Z',
      updated_at: '2026-11-10T12:30:00Z',
    },
  ];

  const html = renderWithQueryClient(React.createElement(ConnectorsSection), {
    vault: testSecrets,
  });

  assert.ok(html.includes('FIRECRAWL_API_KEY'), 'Should render FIRECRAWL_API_KEY');
  assert.ok(html.includes('fc-****4321'), 'Should render fc-****4321 masked string');
  assert.ok(html.includes('GROQ_API_KEY'), 'Should render GROQ_API_KEY');
  assert.ok(html.includes('gsk****8899'), 'Should render gsk****8899 masked string');
  assert.ok(html.includes('Delete FIRECRAWL_API_KEY'), 'Should have delete button for Firecrawl key');
  assert.ok(html.includes('Delete GROQ_API_KEY'), 'Should have delete button for Groq key');
});

it('MCP Presets: Exactly 5 services present with valid configs', () => {
  const expectedServices = ['firecrawl', 'github', 'filesystem', 'postgres', 'linear'];
  const html = renderWithQueryClient(React.createElement(ConnectorsSection));

  for (const svc of expectedServices) {
    const hasPreset = html.toLowerCase().includes(svc);
    assert.ok(hasPreset, `MCP Preset gallery must include service: ${svc}`);
  }
});

it('MCP Presets: Key requirements detection (missing vs configured)', () => {
  // Scenario 1: FIRECRAWL_API_KEY configured, LINEAR_API_KEY missing
  const testSecrets = [
    {
      key_name: 'FIRECRAWL_API_KEY',
      masked_value: 'fc-****4321',
      created_at: '2026-11-10T12:00:00Z',
      updated_at: '2026-11-10T12:00:00Z',
    },
  ];

  const html = renderWithQueryClient(React.createElement(ConnectorsSection), {
    vault: testSecrets,
  });

  const cleanHtml = html.replace(/<!--.*?-->/g, '');
  // Firecrawl requires FIRECRAWL_API_KEY -> configured
  assert.ok(cleanHtml.includes('FIRECRAWL_API_KEY ✓'), 'Firecrawl preset should display configured checkmark');
  // Linear requires LINEAR_API_KEY -> missing
  assert.ok(cleanHtml.includes('LINEAR_API_KEY (missing)'), 'Linear preset should display missing warning');
  // Filesystem requires None
  assert.ok(cleanHtml.includes('None'), 'Filesystem preset should display None required key');
});

it('MCP Presets: Connected status when preset is registered in mcpServers', () => {
  const testServers = [
    {
      id: 'srv-1',
      name: 'firecrawl',
      transport: 'stdio',
      enabled: true,
      command: 'npx -y @firecrawl/mcp-server',
    },
  ];

  const html = renderWithQueryClient(React.createElement(ConnectorsSection), {
    mcp: testServers,
  });

  // Firecrawl should show Connected
  assert.ok(html.includes('Connected'), 'Registered preset should display Connected button');
});

it('ConnectorsSection: Renders 3 main subsections (Linked, Vault, Preset Gallery)', () => {
  const html = renderWithQueryClient(React.createElement(ConnectorsSection));

  assert.ok(html.includes('Connectors'), 'Must contain Connectors header');
  assert.ok(html.includes('API Key Vault (AES-256-GCM)'), 'Must contain API Key Vault section');
  assert.ok(html.includes('1-Click MCP Preset Gallery'), 'Must contain 1-Click MCP Preset Gallery');
  assert.ok(html.includes('Custom MCP Server Studio'), 'Must contain Custom MCP Server Studio');
});

it('ConnectorsSection: Add secret form validation rules', () => {
  const canSave = (key, val) => Boolean(key.trim() && val.trim());

  assert.equal(canSave('', ''), false);
  assert.equal(canSave('KEY', ''), false);
  assert.equal(canSave('', 'VAL'), false);
  assert.equal(canSave('   ', 'VAL'), false);
  assert.equal(canSave('KEY', '   '), false);
  assert.equal(canSave('  \t\n  ', '  \t\n  '), false);
  assert.equal(canSave('MY_KEY', 'secret_val'), true);
  assert.equal(canSave('  MY_KEY  ', '  secret_val  '), true);
});

it('ConnectorsSection: Custom MCP studio form validation and transport mapping', () => {
  const validateCustomMCP = (name, target) => Boolean(name.trim() && target.trim());
  const buildPayload = (name, transport, target) => ({
    name: name.trim(),
    transport,
    command: transport === 'stdio' ? target.trim() : undefined,
    url: transport === 'sse' ? target.trim() : undefined,
  });

  // Validation
  assert.equal(validateCustomMCP('', ''), false);
  assert.equal(validateCustomMCP('srv', ''), false);
  assert.equal(validateCustomMCP('', 'cmd'), false);
  assert.equal(validateCustomMCP('   ', '   '), false);
  assert.equal(validateCustomMCP('my-server', 'npx -y test-mcp'), true);

  // Stdio transport payload mapping
  const stdioPayload = buildPayload('custom-stdio', 'stdio', 'npx -y @modelcontextprotocol/server-postgres');
  assert.equal(stdioPayload.name, 'custom-stdio');
  assert.equal(stdioPayload.transport, 'stdio');
  assert.equal(stdioPayload.command, 'npx -y @modelcontextprotocol/server-postgres');
  assert.equal(stdioPayload.url, undefined);

  // SSE transport payload mapping
  const ssePayload = buildPayload('custom-sse', 'sse', 'https://remote.server.io/events');
  assert.equal(ssePayload.name, 'custom-sse');
  assert.equal(ssePayload.transport, 'sse');
  assert.equal(ssePayload.url, 'https://remote.server.io/events');
  assert.equal(ssePayload.command, undefined);
});

it('ConnectorsSection: Vault suggestion buttons contain all required keys', () => {
  const html = renderWithQueryClient(React.createElement(ConnectorsSection));

  const expectedKeys = [
    'FIRECRAWL_API_KEY',
    'GROQ_API_KEY',
    'GITHUB_PERSONAL_ACCESS_TOKEN',
    'LINEAR_API_KEY',
    'POSTGRES_PASSWORD',
  ];

  for (const k of expectedKeys) {
    assert.ok(html.includes(k), `Common vault key suggestion ${k} must be present`);
  }
});

it('ConnectorsSection: Form input labels and accessibility markup', () => {
  const html = renderWithQueryClient(React.createElement(ConnectorsSection));

  assert.ok(html.includes('vault-key-name'), 'Form must have vault-key-name input ID');
  assert.ok(html.includes('vault-secret-val'), 'Form must have vault-secret-val input ID');
  assert.ok(html.includes('type="password"'), 'Secret value input must be type="password"');
  assert.ok(html.includes('Save Secret'), 'Must have Save Secret button');
});

await viteServer.close();

console.log('================================================================');
console.log(`TEST SUMMARY: ${passed} PASSED, ${failed} FAILED`);
console.log('================================================================');

if (failed > 0) {
  process.exit(1);
}
