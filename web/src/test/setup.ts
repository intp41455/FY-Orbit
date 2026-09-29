import '@testing-library/jest-dom/vitest';
import { afterEach } from 'vitest';
import { cleanup } from '@testing-library/react';

// Isolate component tests: unmount after each test. Network calls must be
// explicitly mocked per test (we never ship fixtures into components).
afterEach(() => {
  cleanup();
});
