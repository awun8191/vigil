# Multi-Page Build: Admin/Vendor Portal as Separate SPA

## Pattern Overview

Build a separate admin/vendor portal website alongside the main consumer app within the same codebase and Vite project. The two share node_modules, Tailwind config, TypeScript config, and utility functions, but produce independent bundle outputs and are served at different paths.

Proven in session May 2026: built a vendor portal (orders management, dashboard, order history) alongside the Mikkies Cafe consumer app. Vendor portal: ~38KB JS bundle. Main app: ~930KB JS bundle. Both built in 3.85s.

## When to Use

- A project needs both a consumer-facing app and an admin/partner/vendor panel
- The two share the same design system, utility library, or backend
- They should be deployable independently (different cache invalidation, different domains or subpaths)
- Full micro-frontend separation would be over-engineered but a monolith would be a maintenance trap

## Vite Multi-Page Configuration

In `vite.config.ts`, add a `build.rollupOptions.input` mapping with one entry per page:

```typescript
// vite.config.ts
import tailwindcss from '@tailwindcss/vite';
import react from '@vitejs/plugin-react';
import path from 'path';
import { defineConfig } from 'vite';

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, 'src'),  // main app uses @/path
    },
  },
  build: {
    rollupOptions: {
      input: {
        main: path.resolve(__dirname, 'index.html'),        // consumer app
        vendor: path.resolve(__dirname, 'vendor/index.html'), // admin portal
      },
    },
  },
  server: {
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
});
```

## Directory Structure

```
project-root/
├── index.html                  # Main consumer app entry
├── vite.config.ts              # Multi-page build config
├── package.json                # Single package.json — shared dependencies
├── tsconfig.json               # Shared TypeScript config
├── src/                        # Main consumer app
│   ├── main.tsx
│   ├── App.tsx
│   ├── pages/
│   ├── components/
│   ├── hooks/
│   ├── lib/
│   │   └── utils.ts           # Can be imported by vendor via relative path
│   └── ...
├── vendor/                     # Admin/vendor portal — separate entry
│   ├── index.html              # Own HTML file
│   └── src/
│       ├── main.tsx
│       ├── App.tsx             # Own routing (separate from consumer app)
│       ├── pages/
│       │   ├── DashboardPage.tsx
│       │   ├── LiveOrdersPage.tsx
│       │   └── OrderHistoryPage.tsx
│       ├── components/
│       │   └── layout/
│       │       └── VendorLayout.tsx  # Sidebar + content layout
│       └── lib/
│           └── api.ts          # API client for vendor endpoints
└── dist/                       # Build output
    ├── index.html              # Main app
    ├── vendor/index.html       # Vendor portal
    └── assets/
        ├── main-*.css
        ├── main-*.js
        └── vendor-*.js         # Separate bundle, no Firebase/recharts/etc.
```

## Key Patterns

### Shared utilities via relative imports

The vendor portal can import shared utility functions from the main app's source using relative paths:

```typescript
// vendor/src/components/layout/VendorLayout.tsx
import { cn } from '../../../src/lib/utils';
```

This avoids duplicating code while keeping the vendor portal physically separated. The `cn` utility (clsx + tailwind-merge) is lightweight and worth sharing. Avoid importing heavy dependencies like Firebase, recharts, or React Context from the main app — the vendor portal should have its own data layer.

### Separate routing, identical styling

The vendor portal uses its own routing and layout, but the same Tailwind CSS classes work because Vite bundles Tailwind once and applies it globally:

```typescript
// vendor/src/App.tsx — own routes
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import VendorLayout from './components/layout/VendorLayout';
import DashboardPage from './pages/DashboardPage';
import LiveOrdersPage from './pages/LiveOrdersPage';

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<VendorLayout />}>
          <Route path="/" element={<DashboardPage />} />
          <Route path="/orders/live" element={<LiveOrdersPage />} />
          <Route path="*" element={<Navigate to="/" />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
```

### Build output

```
dist/index.html                    # Consumer app → serve at /
dist/vendor/index.html             # Vendor portal → serve at /vendor
dist/assets/main-*.css             # Shared CSS
dist/assets/main-*.js              # Consumer app JS (~930KB with Firebase)
dist/assets/vendor-*.js            # Vendor portal JS (~38KB, no Firebase)
```

Deploy `dist/` as a static site. Configure your web server to serve `/vendor/*` paths by rewriting to `dist/vendor/index.html` (for client-side routing).

## Pitfalls

1. **Don't import heavy consumer code** — The vendor portal should NOT import Firebase, recharts, motion, or consumer-specific React Context. Keep it lean. It only needs API calls and basic UI components.

2. **Watch the alias** — The `@` alias in vite.config.ts resolves to `src/` for the consumer app. The vendor portal uses relative paths (`../../../src/lib/utils`) instead. Make sure tsconfig paths don't conflict.

3. **Separate tsconfig if needed** — If the vendor portal needs different compiler options (e.g., no DOM types), create a `vendor/tsconfig.json` and extend the root one.

4. **No shared state** — The vendor portal and consumer app are completely separate React apps at runtime. They don't share state or contexts. All cross-communication goes through the backend API.

5. **CSS collision risk** — Both apps use Tailwind utility classes, which are global. This is fine since they're never loaded on the same page simultaneously. But if you add custom CSS (not Tailwind), use scoped class names to avoid collisions.
