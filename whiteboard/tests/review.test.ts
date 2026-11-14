import assert from 'node:assert/strict';
import test from 'node:test';
import type * as ReviewModule from '../lib/review';

// URL import lets Node's built-in TypeScript runner load the source directly.
const review: typeof ReviewModule = await import(new URL('../lib/review.ts', import.meta.url).href);
const { validateReviewRequest, validateReviewResult, reviewBoard, ReviewError, MAX_IMAGE_BYTES, REVIEW_TIMEOUT_MS } = review;

const image = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aYoQAAAAASUVORK5CYII=';
const request: ReviewModule.ReviewRequest = { image, problem: 'Solve 2x + 3 = 11', mode: 'hint' };
const fakeKey = 'test-key-not-a-real-credential';

function result(overrides: Partial<ReviewModule.ReviewResult> = {}): ReviewModule.ReviewResult {
  return {
    status: 'correct',
    transcription: '2x + 3 = 11\n2x = 8',
    steps: [
      { line: '2x + 3 = 11', status: 'correct', feedback: 'This is the starting equation.' },
      { line: '2x = 8', status: 'correct', feedback: 'You subtracted 3 from both sides.' },
    ],
    title: 'Your steps look good',
    hint: 'What operation would isolate x?',
    explanation: '',
    ...overrides,
  };
}

function successfulResponse(value: unknown): Response {
  return Response.json({ candidates: [{ finishReason: 'STOP', content: { parts: [{ text: JSON.stringify(value) }] } }] });
}

function fetchReturning(response: Response): typeof fetch {
  return async () => response;
}

function hasError(code: string, status: number): (error: unknown) => boolean {
  return (error) => {
    assert.ok(error instanceof ReviewError);
    assert.equal(error.code, code);
    assert.equal(error.status, status);
    return true;
  };
}

test('request validation accepts a PNG and supplies safe feedback defaults', () => {
  assert.deepEqual(validateReviewRequest({ image }), { image, problem: '', mode: 'hint' });
  assert.equal(validateReviewRequest({ ...request, problem: '  Solve x + 1 = 3  ' }).problem, 'Solve x + 1 = 3');
});

test('request validation rejects malformed images, wrong types, oversized prompts, and unsupported modes', () => {
  for (const invalid of [null, [], 'board']) {
    assert.throws(() => validateReviewRequest(invalid), hasError('INVALID_REQUEST', 400));
  }
  for (const invalidImage of ['https://example.com/board.png', 'data:image/jpeg;base64,abc=', 'data:image/png;base64,garbage=', image.slice(0, -1)]) {
    assert.throws(() => validateReviewRequest({ ...request, image: invalidImage }), hasError('INVALID_IMAGE', 400));
  }
  assert.throws(() => validateReviewRequest({ ...request, problem: 'x'.repeat(2_001) }), hasError('INVALID_PROBLEM', 400));
  assert.throws(() => validateReviewRequest({ ...request, mode: 'solve' }), hasError('INVALID_MODE', 400));
});

test('decoded image size limit rejects payloads over 4 MiB before a provider request', () => {
  const oversized = `data:image/png;base64,iVBORw0KGgo${'A'.repeat(Math.ceil(MAX_IMAGE_BYTES / 3) * 4)}`;
  assert.throws(() => validateReviewRequest({ ...request, image: oversized }), hasError('IMAGE_TOO_LARGE', 413));
});

test('output normalization prevents a green overall result when any step is mistaken or uncertain', () => {
  const step = { line: '2x = 14', status: 'mistake' as const, feedback: 'Check the subtraction.' };
  assert.equal(validateReviewResult(result({ steps: [step] })).status, 'mistake');
  assert.equal(validateReviewResult(result({ steps: [{ ...step, status: 'uncertain' }] })).status, 'uncertain');
  assert.equal(validateReviewResult(result({ status: 'empty' })).status, 'uncertain');
  assert.equal(validateReviewResult(result({ steps: [] })).status, 'uncertain');
});

test('model output requires bounded, correctly typed feedback and a summary', () => {
  for (const malformed of [
    null,
    { ...result(), status: 'probably' },
    { ...result(), steps: [{ line: 'x = 1', status: 'correct' }] },
    { ...result(), title: '   ' },
    { ...result(), transcription: 42 },
    { ...result(), steps: Array(61).fill(result().steps[0]) },
  ]) {
    assert.throws(() => validateReviewResult(malformed), hasError('INVALID_RESPONSE', 502));
  }
});

test('review keeps credentials in the header and sends only PNG bytes with structured output settings', async () => {
  let calls = 0;
  const fakeFetch: typeof fetch = async (url, init) => {
    calls += 1;
    assert.match(String(url), /\/models\/test-model:generateContent$/);
    assert.ok(!String(url).includes(fakeKey));
    assert.equal(new Headers(init?.headers).get('x-goog-api-key'), fakeKey);
    assert.equal(init?.method, 'POST');
    const body = JSON.parse(String(init?.body));
    assert.equal(body.contents[0].parts[1].inlineData.data, image.split(',')[1]);
    assert.equal(body.contents[0].parts[1].inlineData.mimeType, 'image/png');
    assert.equal(body.generationConfig.responseMimeType, 'application/json');
    assert.ok(body.generationConfig.responseJsonSchema.properties.steps);
    assert.ok(!String(init?.body).includes(fakeKey));
    return successfulResponse(result());
  };
  assert.deepEqual(await reviewBoard(request, { apiKey: fakeKey, model: 'test-model', fetch: fakeFetch }), result());
  assert.equal(calls, 1);
});

test('first-mistake feedback preserves the identified error and uncertain downstream work', async () => {
  const firstMistake = result({
    status: 'mistake',
    transcription: '2x + 3 = 11\n2x = 14\nx = 7',
    steps: [
      { line: '2x + 3 = 11', status: 'correct', feedback: 'This is the starting equation.' },
      { line: '2x = 14', status: 'mistake', feedback: 'Subtracting 3 from 11 does not give 14.' },
      { line: 'x = 7', status: 'uncertain', feedback: 'Revisit the previous line first.' },
    ],
    title: 'Check the subtraction',
    hint: 'Try subtracting 3 from both sides.',
  });
  const actual = await reviewBoard(request, { apiKey: fakeKey, fetch: fetchReturning(successfulResponse(firstMistake)) });
  assert.deepEqual(actual, firstMistake);
  assert.equal(actual.steps.findIndex((step) => step.status === 'mistake'), 1);
});

test('hint mode suppresses an unsolicited explanation, while explain mode preserves it', async () => {
  const answer = result({ explanation: 'Divide both sides by 2 to isolate x.' });
  const hinted = await reviewBoard(request, { apiKey: fakeKey, fetch: fetchReturning(successfulResponse(answer)) });
  assert.equal(hinted.explanation, '');
  const explained = await reviewBoard({ ...request, mode: 'explain' }, { apiKey: fakeKey, fetch: fetchReturning(successfulResponse(answer)) });
  assert.equal(explained.explanation, answer.explanation);
});

test('missing credentials and invalid model configuration fail without making a request', async () => {
  let calls = 0;
  const fakeFetch: typeof fetch = async () => { calls += 1; return successfulResponse(result()); };
  await assert.rejects(reviewBoard(request, { apiKey: undefined, fetch: fakeFetch }), hasError('MISSING_API_KEY', 503));
  await assert.rejects(reviewBoard(request, { apiKey: fakeKey, model: '../wrong?key=oops', fetch: fakeFetch }), hasError('INVALID_MODEL', 503));
  assert.equal(calls, 0);
});

test('provider authentication failures are sanitized, including API_KEY_INVALID returned as HTTP 400', async () => {
  for (const status of [401, 403]) {
    await assert.rejects(reviewBoard(request, {
      apiKey: fakeKey,
      fetch: fetchReturning(Response.json({ error: { message: `provider secret: ${fakeKey}` } }, { status })),
    }), (error: unknown) => {
      hasError('INVALID_API_KEY', 503)(error);
      assert.ok(!(error as Error).message.includes(fakeKey));
      assert.ok(!(error as Error).message.includes('provider secret'));
      return true;
    });
  }
  await assert.rejects(reviewBoard(request, {
    apiKey: fakeKey,
    fetch: fetchReturning(Response.json({ error: { details: [{ reason: 'API_KEY_INVALID' }] } }, { status: 400 })),
  }), hasError('INVALID_API_KEY', 503));
});

test('quota errors retain a bounded retry delay and never retry automatically', async () => {
  let calls = 0;
  const fakeFetch: typeof fetch = async () => {
    calls += 1;
    return new Response('quota', { status: 429, headers: { 'retry-after': '999999' } });
  };
  await assert.rejects(reviewBoard(request, { apiKey: fakeKey, fetch: fakeFetch }), (error: unknown) => {
    hasError('RATE_LIMITED', 429)(error);
    assert.equal((error as InstanceType<typeof ReviewError>).retryAfter, 300);
    return true;
  });
  assert.equal(calls, 1);
});

test('missing candidates, truncated generation, malformed JSON, and wrong response fields are rejected', async () => {
  const cases: Array<[Response, string]> = [
    [Response.json({ candidates: [] }), 'EMPTY_RESPONSE'],
    [Response.json({ candidates: [{ finishReason: 'MAX_TOKENS' }] }), 'INCOMPLETE_RESPONSE'],
    [Response.json({ candidates: [{ content: { parts: [{ text: '{broken' }] } }] }), 'INVALID_RESPONSE'],
    [successfulResponse({ ...result(), steps: 'not steps' }), 'INVALID_RESPONSE'],
  ];
  for (const [response, code] of cases) {
    await assert.rejects(reviewBoard(request, { apiKey: fakeKey, fetch: fetchReturning(response) }), hasError(code, 502));
  }
});

test('network exceptions are sanitized and external cancellation gets a distinct error', async () => {
  const brokenFetch: typeof fetch = async () => { throw new Error(`request with ${fakeKey} failed`); };
  await assert.rejects(reviewBoard(request, { apiKey: fakeKey, fetch: brokenFetch }), (error: unknown) => {
    hasError('NETWORK_ERROR', 502)(error);
    assert.ok(!(error as Error).message.includes(fakeKey));
    return true;
  });
  const controller = new AbortController();
  controller.abort();
  const abortedFetch: typeof fetch = async (_url, init) => {
    assert.equal(init?.signal?.aborted, true);
    throw new DOMException('aborted', 'AbortError');
  };
  await assert.rejects(reviewBoard(request, { apiKey: fakeKey, signal: controller.signal, fetch: abortedFetch }), hasError('REVIEW_CANCELLED', 499));
});

test('a stalled provider request aborts after the timeout without a real-time wait', async (context) => {
  context.mock.timers.enable({ apis: ['setTimeout'] });
  let aborted = false;
  const stalledFetch: typeof fetch = async (_url, init) => new Promise<Response>((_resolve, reject) => {
    init?.signal?.addEventListener('abort', () => {
      aborted = true;
      reject(new DOMException('aborted', 'AbortError'));
    }, { once: true });
  });
  const pending = reviewBoard(request, { apiKey: fakeKey, fetch: stalledFetch });
  const rejection = assert.rejects(pending, hasError('REVIEW_TIMEOUT', 504));
  context.mock.timers.tick(REVIEW_TIMEOUT_MS);
  await rejection;
  assert.equal(aborted, true);
});
