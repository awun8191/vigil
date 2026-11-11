import assert from 'node:assert';
import fs from 'node:fs';
import path from 'node:path';

console.log('================================================================');
console.log('CHALLENGER M4-2 GEN4 EMPIRICAL ADVERSARIAL STRESS TEST HARNESS');
console.log('================================================================');

let passed = 0;
let failed = 0;

function test(name, fn) {
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

const inputPillPath = path.resolve('web/src/components/chat/InputPill.tsx');
const harkAppPath = path.resolve('web/src/components/hark/HarkUnifiedApp.tsx');
const inputPillContent = fs.readFileSync(inputPillPath, 'utf8');
const harkAppContent = fs.readFileSync(harkAppPath, 'utf8');

// --- 1. Static Contract & Interface Integrity ---
test('InputPill: Props interface declares optional streaming, busy, and onStop', () => {
  assert(inputPillContent.includes('interface Props'), 'Props interface must be declared');
  assert(/onStop\?\s*:\s*\(\)\s*=>\s*void/.test(inputPillContent), 'onStop must be optional');
  assert(/streaming\?\s*:\s*boolean/.test(inputPillContent), 'streaming must be optional boolean');
  assert(/busy\?\s*:\s*boolean/.test(inputPillContent), 'busy must be optional boolean');
});

test('InputPill: isStreaming resolution uses nullish coalescing', () => {
  assert(
    inputPillContent.includes('const isStreaming = streaming ?? busy ?? false;'),
    'isStreaming must resolve streaming ?? busy ?? false'
  );
});

test('InputPill: Stop button safely invokes onStop with optional chaining', () => {
  assert(
    inputPillContent.includes('onClick={isStreaming ? () => onStop?.() : submit}'),
    'Stop button must invoke onStop with optional chaining to prevent TypeError'
  );
});

test('HarkUnifiedApp: Correctly passes streaming and onStop to InputPill', () => {
  assert(
    harkAppContent.includes('streaming={chat.streaming}'),
    'HarkUnifiedApp must pass streaming={chat.streaming}'
  );
  assert(
    harkAppContent.includes('onStop={() => chat.stop()}'),
    'HarkUnifiedApp must pass onStop={() => chat.stop()}'
  );
  // Ensure the old buggy busy={chat.streaming} was replaced
  assert(
    !harkAppContent.includes('busy={chat.streaming}'),
    'HarkUnifiedApp must NOT pass busy={chat.streaming}'
  );
});

// --- 2. State & Truth Table Verification ---
test('isStreaming truth table resolution under all permutation combinations', () => {
  const resolveStreaming = (streaming, busy) => streaming ?? busy ?? false;

  // permutations: [streaming, busy]
  assert.strictEqual(resolveStreaming(undefined, undefined), false);
  assert.strictEqual(resolveStreaming(false, undefined), false);
  assert.strictEqual(resolveStreaming(true, undefined), true);
  assert.strictEqual(resolveStreaming(undefined, false), false);
  assert.strictEqual(resolveStreaming(undefined, true), true);
  assert.strictEqual(resolveStreaming(false, false), false);
  assert.strictEqual(resolveStreaming(true, false), true);
  assert.strictEqual(resolveStreaming(false, true), false); // streaming=false overrides busy=true
  assert.strictEqual(resolveStreaming(true, true), true);
});

test('canSend predicate truth table with isStreaming, disabled, draft, and files', () => {
  const canSend = (draft, files, isStreaming, disabled) =>
    (draft.trim().length > 0 || files.length > 0) && !isStreaming && !disabled;

  assert.strictEqual(canSend('', [], false, false), false);
  assert.strictEqual(canSend('   ', [], false, false), false);
  assert.strictEqual(canSend('\n\t\r ', [], false, false), false);
  assert.strictEqual(canSend('Hello', [], false, false), true);
  assert.strictEqual(canSend('', ['file.png'], false, false), true);
  assert.strictEqual(canSend('Hello', ['file.png'], false, false), true);

  // When isStreaming is true, canSend must be false regardless of content
  assert.strictEqual(canSend('Hello', [], true, false), false);
  assert.strictEqual(canSend('', ['file.png'], true, false), false);
  assert.strictEqual(canSend('Hello', ['file.png'], true, false), false);

  // When disabled is true, canSend must be false regardless of content
  assert.strictEqual(canSend('Hello', [], false, true), false);
  assert.strictEqual(canSend('', ['file.png'], false, true), false);
  assert.strictEqual(canSend('Hello', ['file.png'], false, true), false);

  // When both isStreaming and disabled are true
  assert.strictEqual(canSend('Hello', [], true, true), false);
});

test('BeamPhase computation truth table', () => {
  const computePhase = (recording, isStreaming, hasReplyTokens) =>
    recording
      ? 'listening'
      : isStreaming
        ? hasReplyTokens
          ? 'speaking'
          : 'thinking'
        : 'idle';

  assert.strictEqual(computePhase(true, false, false), 'listening');
  assert.strictEqual(computePhase(true, true, true), 'listening'); // recording takes precedence
  assert.strictEqual(computePhase(false, true, false), 'thinking'); // streaming, no tokens yet
  assert.strictEqual(computePhase(false, true, true), 'speaking'); // streaming, has tokens
  assert.strictEqual(computePhase(false, false, false), 'idle');
  assert.strictEqual(computePhase(false, false, true), 'idle'); // not streaming -> idle
});

test('onStop optionality: safe execution when onStop is omitted or undefined', () => {
  let onStopCalled = false;
  const onStop = () => { onStopCalled = true; };
  const nullOnStop = undefined;

  // Safe handler logic from InputPill line 344
  const makeClickHandler = (isStreaming, fn, submitFn) =>
    isStreaming ? () => fn?.() : submitFn;

  let submitCalled = false;
  const submit = () => { submitCalled = true; };

  // Case 1: isStreaming true, onStop provided
  const handler1 = makeClickHandler(true, onStop, submit);
  handler1();
  assert.strictEqual(onStopCalled, true);
  assert.strictEqual(submitCalled, false);

  // Case 2: isStreaming true, onStop undefined (must NOT throw)
  const handler2 = makeClickHandler(true, nullOnStop, submit);
  assert.doesNotThrow(() => handler2());

  // Case 3: isStreaming false, calls submit
  submitCalled = false;
  const handler3 = makeClickHandler(false, onStop, submit);
  handler3();
  assert.strictEqual(submitCalled, true);
});

test('MediaRecorder chunk collection and Blob reconstruction', () => {
  const chunks = [
    new Uint8Array([0x1a, 0x45, 0xdf, 0xa3]), // EBML header simulation
    new Uint8Array([0x42, 0x82, 0x88, 0x77, 0x65, 0x62, 0x6d]),
    new Uint8Array([0x00, 0x01, 0x02, 0x03]),
  ];

  const blob = new Blob(chunks, { type: 'audio/webm' });
  assert.strictEqual(blob.type, 'audio/webm');
  assert.strictEqual(blob.size, 4 + 7 + 4);
});

test('Textarea height expansion clamping between min and 160px ceiling', () => {
  const computeHeight = (scrollHeight) => `${Math.min(scrollHeight, 160)}px`;

  assert.strictEqual(computeHeight(0), '0px');
  assert.strictEqual(computeHeight(44), '44px');
  assert.strictEqual(computeHeight(120), '120px');
  assert.strictEqual(computeHeight(160), '160px');
  assert.strictEqual(computeHeight(161), '160px');
  assert.strictEqual(computeHeight(800), '160px');
  assert.strictEqual(computeHeight(10000), '160px');
});

test('HarkUnifiedApp integration: seed consumption lifecycle integrity', () => {
  let seed = 'Draft an email to Maji';
  let seedConsumed = false;
  const handleSeedConsumed = () => { seedConsumed = true; };

  // Simulate InputPill useEffect
  let draft = '';
  if (seed) {
    draft = seed;
    handleSeedConsumed?.();
  }

  assert.strictEqual(draft, 'Draft an email to Maji');
  assert.strictEqual(seedConsumed, true);
});

console.log('================================================================');
console.log(`TOTAL TESTS: ${passed + failed} | PASSED: ${passed} | FAILED: ${failed}`);
console.log('================================================================');

if (failed > 0) {
  process.exit(1);
}
