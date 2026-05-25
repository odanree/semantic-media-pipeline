/**
 * Vitest global setup — runs once before all tests
 * Centralize mocks and polyfills here to avoid re-processing in every test file
 */

import { vi } from 'vitest'

// ── jsdom Polyfills (hoisted globally) ────────────────────────────────────────

if (typeof window !== 'undefined') {
  // ResultGrid uses IntersectionObserver for lazy-loading
  Object.defineProperty(window, 'IntersectionObserver', {
    writable: true,
    configurable: true,
    value: class IntersectionObserverMock {
      observe = vi.fn()
      unobserve = vi.fn()
      disconnect = vi.fn()
      constructor(_cb: IntersectionObserverCallback) {}
    },
  })

  // ResultGrid calls scrollIntoView on page change
  window.HTMLElement.prototype.scrollIntoView = vi.fn()
}

// ── Global Mocks (hoisted before all test imports) ───────────────────────────

vi.mock('next/image', () => ({
  default: function MockImage(props: Record<string, unknown>) {
    const { src, alt, fill, priority, ...rest } = props as {
      src: string
      alt: string
      fill?: boolean
      priority?: boolean
      [k: string]: unknown
    }
    // eslint-disable-next-line @next/next/no-img-element
    const React = require('react')
    return React.createElement('img', { src, alt, 'data-fill': fill, 'data-priority': priority, ...rest })
  },
}))

// NOTE: useStatusUpdates and useMediaUpdates are NOT mocked here because
// hooks.test.ts needs to test their actual WebSocket integration behavior.
// These hooks are tested with a FakeWebSocket implementation in their test file.

vi.mock('@/components/HighlightReelPlayer', () => ({
  default: function MockHighlightReelPlayer({ onClose }: { onClose: () => void }) {
    const React = require('react')
    return React.createElement('div', { 'data-testid': 'highlight-reel', onClick: onClose }, 'MockReel')
  },
}))
