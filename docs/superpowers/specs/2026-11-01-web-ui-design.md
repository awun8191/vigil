# Design Specification: Virgil Web Client & Interaction Surface

- **Date:** 2026-11-01
- **Status:** Approved
- **Author:** Virgil Core Team
- **Target Surface:** `web/`

---

## 1. Vision & Mental Model

Virgil is an open-source, self-hostable proactive AI assistant and execution harness. Rather than a blunt, industrial DevOps terminal or a generic AI chatbot, the Virgil web application is conceived as a **comforting, warm, sleek, and comforting digital companion**.

Drawing inspiration from **Apple’s fluid tactile ergonomics**, **Google DeepMind’s calm atmospheric intelligence**, and **Hark’s pillowy, welcoming warmth**, Virgil rejects AI tropes (harsh purple gradients, cluttered cards, and dark mesh textures) in favor of serene ambient glows, frosted glass, and squircle geometry.

The product is purposefully architected around two primary everyday views, controlled by a hovering pill dock:
1. **Home (`/`):** A calm daily briefing surface presenting tasks identified proactively by Virgil, gentle contextual reminders, and pre-configured routines.
2. **Chat (`/chat`):** A distraction-free conversational canvas anchored by a floating, minimal input pill with file attachment and voice recording capabilities.

---

## 2. Visual Aesthetic & Design Tokens

### 2.1 Theme Palettes

The interface features two meticulously crafted themes:

#### Dark Theme ("Deep Navy & Ethereal Sky")
- **Base Background:** Deepest obsidian black (`#070A10`) blending into midnight navy (`#0A0F1D`).
- **Surface Elevation:** Frosted translucent glass (`rgba(255, 255, 255, 0.04)` to `rgba(255, 255, 255, 0.07)`).
- **Glass Hairlines:** Sub-pixel translucent borders (`border border-white/[0.08]`).
- **Glow & Accent:** Ethereal sky blue (`#38BDF8` / `#60A5FA`) used for active indicators, voice activity waveforms, and breathing thought rings.
- **Text:** Crisp milk-white for headings (`#F8FAFC`), muted slate-blue for secondary metadata (`#94A3B8`).

#### Light Theme ("Warm Cream & Ivory")
- **Base Background:** Warm, organic tactile cream (`#FBF9F5` / `#F7F5EE`).
- **Surface Elevation:** Pure floating white cards (`#FFFFFF`) with pillowy soft edges.
- **Glass Hairlines:** Soft, warm whisper borders (`border border-black/[0.05]`).
- **Glow & Accent:** Deep navy slate (`#0F172A`) paired with sky-blue accents (`#0284C7`).
- **Text:** Deep charcoal slate (`#0F172A`), warm stone gray (`#64748B`).

### 2.2 Typography
- **Primary Typeface:** `Plus Jakarta Sans` (weights: 400 Regular, 500 Medium, 600 Semi-Bold, 700 Bold).
- **Styling:** Generous tracking (`tracking-[-0.01em]` to `tracking-[-0.02em]` on headings), relaxed line-heights (`leading-relaxed`), and humane, friendly tone across all UI copy.

### 2.3 Geometry & Haptics
- **Squircles:** Generous organic curves (`rounded-[28px]` and `rounded-3xl` for cards, `rounded-full` for docks and pills).
- **Nested Radii:** Child containers match mathematically calculated inner radii (`calc(28px - padding)`) to ensure perfect concentric curves.
- **Ambient Shadows:** Multi-layered diffuse shadows (`shadow-[0_20px_50px_rgba(0,0,0,0.25)]`) eliminating harsh 1px borders.
- **Spring Physics:** Motion transitions use spring curves (`stiffness: 400, damping: 30`, or `cubic-bezier(0.25, 1, 0.5, 1)`).

### 2.4 Iconography Standard (Strict Rule: Zero Emojis)
- **Library:** `lucide-react` exclusively.
- **Strict Directive:** **Zero emojis as UI icons anywhere in the application.** Every action, navigation item, status indicator, and attachment trigger must use crisp, high-quality vector icons from `lucide-react` (styled with `strokeWidth={1.5}` or `strokeWidth={1.75}` for light Apple-esque precision).
- **Icon Mapping:**
  - Home: `<Home size={18} strokeWidth={1.5} />`
  - Chat: `<MessageSquare size={18} strokeWidth={1.5} />`
  - Settings: `<Settings size={18} strokeWidth={1.5} />`
  - Attach / Upload: `<Plus size={18} strokeWidth={1.5} />` or `<Paperclip size={18} strokeWidth={1.5} />`
  - Audio / Voice: `<Mic size={18} strokeWidth={1.5} />`
  - Send: `<ArrowUp size={18} strokeWidth={1.75} />`
  - Action / Approval: `<Check size={16} strokeWidth={1.75} />`, `<ExternalLink size={14} strokeWidth={1.5} />`
  - Reminders: `<Clock size={16} strokeWidth={1.5} />`
  - Routines / Workflows: `<Sparkles size={16} strokeWidth={1.5} />` or `<Zap size={16} strokeWidth={1.5} />`
  - Theme Toggle: `<Sun size={16} strokeWidth={1.5} />` and `<Moon size={16} strokeWidth={1.5} />`

---

## 3. Global Navigation: The Hovering Side Pill Dock

There is **no top navbar**. The top of the screen remains completely airy and uncluttered, bearing only a discrete, minimalist brand mark in the upper left corner: `Virgil ✦`.

### 3.1 Dock Anatomy
- **Positioning:** Hovering vertically along the left viewport edge, completely detached from the screen boundaries (`left-6`, vertically centered or floating in upper-middle).
- **Form:** An ultra-compact, rounded squircle pill (`rounded-full`, `p-2`, `backdrop-blur-2xl`, glass elevation).
- **Core Items (3 only, Lucide vector icons):**
  1. **Home (`Home` icon):** Navigates to the proactive task briefing.
  2. **Chat (`MessageSquare` icon):** Navigates to the conversational canvas.
  3. **Settings (`Settings` icon):** Opens the floating frosted settings sheet.
- **Active State:** The active item receives a glowing sky-blue background capsule with a smooth spring transition (`layoutId="activeDockIndicator"`).
- **Mobile Adaptation:** On viewport widths `< 768px`, the dock relocates to the bottom center of the screen as a floating iOS-style bottom pill bar, preserving one-thumb ergonomics.

---

## 4. Section Specification: The Home View

The Home view serves as the user's peaceful daily command briefing. It eliminates administrative noise and presents only high-signal items synthesized by Virgil.

### 4.1 Header & Status Area
- **Time-Aware Greeting:** E.g., *"Good morning. Virgil has organized 3 items for you today."*
- **Empty / Zen State:** When no items require attention, the view displays a calm ambient message: *"You're all caught up. Virgil is monitoring in the background."* with a slow, soothing radial breathing glow.

### 4.2 Proactive Card Feed
Cards are displayed in a spacious, responsive vertical or bento layout:

1. **Action Ready (Triage & Approvals):**
   - High-confidence actions synthesized from background connectors (e.g., an email reply drafted, a calendar conflict resolved).
   - Card anatomy:
     - Pill tag: `✦ ACTION READY` in sky blue.
     - Title and context preview (e.g., *"Reply drafted for Sarah: Q4 Infrastructure budget"*).
     - Inline pill actions: `[ Review & Send ]` (primary sky-blue pill), `[ Dismiss ]` (subtle ghost pill).
     - Context Bridge: `Discuss with Virgil ↗` link, which transitions seamlessly into Chat with the task pre-loaded.
2. **Contextual Reminders:**
   - Soft, timely nudges (e.g., *"Design sync in 30 minutes. Prep notes synthesized."*).
   - Inline actions: `[ Open Notes ]`, `[ Snooze ]`.
3. **Pre-Configured Routines:**
   - Saved automations ready to execute on demand (e.g., *"Weekly Client Digest"*, *"Inbox Zero Sprint"*).
   - Inline action: `[ Run Now ↗ ]`.

---

## 5. Section Specification: The Chat View

The Chat view is a clean, centered conversational canvas paired with a signature floating input island.

### 5.1 Conversational Canvas
- **Layout:** Centered column (`max-w-3xl`) with generous top and bottom margins to prevent overlap with the floating dock and input pill.
- **Message Rendering:**
  - **User Messages:** Right-aligned, soft squircle pills with gentle border highlights.
  - **Virgil Messages:** Left-aligned, unboxed natural typography heralded by the `Virgil ✦` mark.
- **Living Thinking & Tool Capsules:**
  - When Virgil is executing background tools or searching connectors, it does not dump raw monospace logs.
  - It displays a compact, frosted **Thinking Capsule**: `✦ Scanning calendar & inbox...` with a subtle pulsating sky-blue halo.
  - Tapping the capsule smoothly unfolds step details, parameters, and outputs in an animated drawer.

### 5.2 The Hero Floating Input Pill
Suspended above the bottom edge of the viewport (`bottom-8`, centered):
- **Form:** Fully rounded pill (`rounded-full`, `px-4 py-2.5`, `max-w-2xl w-full`).
- **Glassmorphism:** Deep blur (`backdrop-blur-2xl`), subtle sky-blue ambient shadow.
- **Controls inside the Pill:**
  - **Left: File / Attachment Button (`+`):** Compact circular button to upload documents, images, or code files.
  - **Center: Text Input:** Auto-expanding field with placeholder *"Ask Virgil anything or assign a task..."*.
  - **Right: Audio / Voice Button (`🎙`):** Circular button for voice input. While active, it morphs into an animated waveform visualizer.
  - **Far Right: Send Button (`↑`):** Circular action button that illuminates in vibrant sky-blue when message or file content is present.

---

## 6. Section Specification: The Settings Experience

Triggered by tapping `⚙` on the hovering dock, the Settings interface opens as a **floating frosted sheet** rather than a disruptive full-page navigation.

### 6.1 Controls & Features
1. **Theme Switcher:** Seamless toggle between:
   - ☼ *Cream & White* (Warm Light Mode)
   - ☾ *Deep Navy & Sky Blue* (Atmospheric Dark Mode)
2. **Device Pairing:**
   - Input to enter pairing code (`VGL-...`) to connect with Virgil’s `/api/v1/auth/pair`.
   - Display option for QR code pairing with mobile devices.
3. **System & Connector Health:**
   - Real-time status badges for the FastAPI backend (`/api/v1/health`), Worker background queue, and MCP connectors (Gmail, Google Calendar).
4. **Model Preferences:**
   - Selection between Speed (Fast tier) and Deep Reasoner (Advanced tier).

---

## 7. Technical Architecture & Directory Structure

### 7.1 Tech Stack
- **Framework:** React 19 + TypeScript + Vite.
- **Styling:** Tailwind CSS (configured with custom tokens for obsidian, navy, sky blue, cream, and warm white).
- **Icons:** Lucide Icons (styled with `strokeWidth={1.5}` for refined Apple-like hairlines).
- **Motion:** Framer Motion (`motion/react`) for layout springs and tab cross-fades.
- **Typography:** `Plus Jakarta Sans` via `@fontsource/plus-jakarta-sans`.

### 7.2 Directory Layout
```
web/
├── index.html
├── package.json
├── tsconfig.json
├── vite.config.ts
├── tailwind.config.js
└── src/
    ├── main.tsx
    ├── App.tsx
    ├── index.css
    ├── types/
    │   └── index.ts                 # Tasks, messages, reminders, pairing types
    ├── store/
    │   └── useAppStore.ts           # Global store (activeTab, theme, tasks, chat)
    ├── services/
    │   ├── api.ts                   # Backend REST client (/api/v1)
    │   ├── sse.ts                   # SSE event streaming client
    │   └── mockData.ts              # High-fidelity standalone simulation data
    └── components/
        ├── layout/
        │   ├── AmbientBackground.tsx # Dual-theme organic radial blurs
        │   ├── FloatingSideDock.tsx  # Hovering 3-icon pill dock
        │   └── Shell.tsx             # Root layout container
        ├── home/
        │   ├── BriefingHeader.tsx    # Time-aware comforting greeting
        │   ├── TaskCard.tsx          # Frosted squircle action/approval card
        │   ├── ReminderCard.tsx      # Contextual reminder card
        │   └── PreconfiguredCard.tsx # Routine quick-launcher
        ├── chat/
        │   ├── MessageStream.tsx     # Conversational message history
        │   ├── MessageItem.tsx       # Formatted user and agent turns
        │   ├── ThinkingCapsule.tsx   # Animated breathing thinking indicator
        │   └── FloatingInputPill.tsx # Floating hero input island with +, mic, send
        └── settings/
            └── SettingsSheet.tsx     # Floating frosted settings modal
```

---

## 8. Dual-Mode Operation (Standalone Simulation & Live Backend)

To provide an immediate, zero-friction developer and user experience:
1. **Interactive Standalone Simulation Mode:**
   - The application functions out-of-the-box with zero backend dependencies.
   - Includes realistic mock tasks, interactive approvals, simulated voice waveforms, and realistic agent streaming responses.
2. **Live Backend Mode:**
   - Easily toggled via environment variable (`VITE_API_URL`) or in Settings.
   - Binds to FastAPI endpoints (`GET /api/v1/health`, `POST /api/v1/auth/pair`, cards, and runs SSE streams).

---

## 9. Verification & Success Criteria

1. **Aesthetic Audit:**
   - Dark theme renders deep navy (`#0A0F1D`) / obsidian (`#070A10`) with glowing sky blue (`#38BDF8`).
   - Light theme renders warm cream (`#FBF9F5`) and pure white cards with soft whisper borders.
   - All cards utilize squircle corners (`rounded-[28px]`) with smooth frosted blurs (`backdrop-blur-2xl`).
2. **Ergonomic Audit:**
   - Hovering side dock is compact, floating, and effortlessly switches between Home and Chat without page reloads.
   - Floating input pill provides instant visual feedback on text entry, file attachment clicks, and voice recording toggles.
   - Settings sheet floats over the active view without losing context.
3. **Build & Performance:**
   - `pnpm run build` or `npm run build` passes with zero TypeScript or linting errors.
   - Page load is instant with silky-smooth 60fps spring transitions.
