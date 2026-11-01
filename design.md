# Virgil Web Design Specification (`design.md`)

> **Design Philosophy: The Auteur Standard**  
> An ultra-clean, comforting, and engaging digital surface. Rejects blunt "DevOps/Docker terminal" aesthetics and generic AI slop in favor of the calm, atmospheric intelligence of **Google DeepMind**, the fluid tactile ergonomics of **Apple**, and the warm, pillowy friendliness of **Hark**.

---

## 1. Core Product Architecture & Mental Model

Virgil is a proactive, self-hostable personal AI assistant and execution harness. Its web interface is intentionally organized into **two primary everyday surfaces**, navigated by a **hovering side pill dock**:

1. **Home (`/`):** The calm morning briefing. Displays tasks Virgil has proactively identified, action approval gates, gentle contextual reminders, and pre-configured routines.
2. **Chat (`/chat`):** The direct conversational canvas. Centered message flow anchored by a signature floating hero input pill with attachment and voice recording capabilities.
3. **Settings (Modal):** A floating frosted glass sheet for theme switching, device pairing, and system health.

**Header Elimination:** There is **no top navbar**. The top of the screen remains completely airy and uncluttered, bearing only a discrete, minimalist brand mark in the upper-left margin: `Virgil ✦`.

---

## 2. Visual Aesthetic & Design Tokens

### 2.1 Dual-Theme Palette

```
┌────────────────────────────────────────┬────────────────────────────────────────┐
│ Dark Theme ("Deep Navy & Ethereal Sky")│ Light Theme ("Warm Cream & Ivory")     │
├────────────────────────────────────────┼────────────────────────────────────────┤
│ Base: #070A10 (Obsidian) → #0A0F1D     │ Base: #FBF9F5 (Warm Cream) → #F7F5EE   │
│ Cards: rgba(255,255,255,0.05) Frosted  │ Cards: #FFFFFF (Pure Soft White)       │
│ Borders: border-white/[0.08] hairlines │ Borders: border-black/[0.05] whisper   │
│ Accent: #38BDF8 (Ethereal Sky Blue)    │ Accent: #0F172A (Navy) & #0284C7 (Sky) │
│ Text: #F8FAFC (Milk) / #94A3B8 (Slate) │ Text: #0F172A (Charcoal) / #64748B     │
└────────────────────────────────────────┴────────────────────────────────────────┘
```

### 2.2 Typography
- **Font Family:** `Plus Jakarta Sans`, sans-serif.
- **Weights:** Regular (400), Medium (500), Semi-Bold (600), Bold (700).
- **Tone:** Human, calm, reassuring, and concise. Comfortable line-heights (`leading-relaxed`) and tracked-out modern headings (`tracking-[-0.015em]`).

### 2.3 Geometry & Tactility
- **Squircles:** Generous organic curves (`rounded-[28px]` and `rounded-3xl` for cards; `rounded-full` for pills and docks).
- **Nested Radii:** Child elements match concentric inner curves (`calc(28px - padding)`).
- **Ambient Shadows:** Multi-layered diffuse drop shadows (`shadow-[0_20px_50px_rgba(0,0,0,0.25)]`) with zero harsh 1px borders.
- **Motion:** Spring physics (`cubic-bezier(0.25, 1, 0.5, 1)`, stiffness 400, damping 30).

---

## 3. Strict Iconography Directive (Zero Emojis)

> [!IMPORTANT]
> **Strict Rule:** **Zero emojis are to be used as icons anywhere in the application.**
> All buttons, navigation items, pills, and indicators must strictly utilize high-quality vector icons from **`lucide-react`** with refined light stroke weights (`strokeWidth={1.5}` or `1.75`).

### Icon Mapping
| Surface / Action | Lucide Icon | Stroke Width | Size |
|---|---|---|---|
| Home Navigation | `<Home />` | `1.5` | 18px |
| Chat Navigation | `<MessageSquare />` | `1.5` | 18px |
| Settings Navigation | `<Settings />` | `1.5` | 18px |
| Attachment / Upload | `<Plus />` or `<Paperclip />` | `1.5` | 18px |
| Audio / Voice Input | `<Mic />` | `1.5` | 18px |
| Send Action | `<ArrowUp />` | `1.75` | 18px |
| Approval / Send | `<Check />` | `1.75` | 16px |
| Dismiss / Close | `<X />` | `1.5` | 16px |
| Reminders | `<Clock />` | `1.5` | 16px |
| Routines / Workflows | `<Zap />` or `<Sparkles />` | `1.5` | 16px |
| Context Bridge | `<ArrowUpRight />` | `1.5` | 14px |
| Theme: Light | `<Sun />` | `1.5` | 16px |
| Theme: Dark | `<Moon />` | `1.5` | 16px |
| Device Pairing | `<Smartphone />` | `1.5` | 16px |

---

## 4. Global Navigation: The Hovering Side Pill Dock

A delicate, compact pill that floats completely detached from the screen edges:

```
┌────────────────────────────────────────────────────────────────────────┐
│ Virgil ✦                                                               │
│                                                                        │
│   ╭───╮                                                                │
│   │ ⌂ │  Home (<Home />)                                               │
│   │   │                                                                │
│   │ 💬│  Chat (<MessageSquare />)                                      │
│   │   │                                                                │
│   │ ⚙ │  Settings (<Settings />)                                       │
│   ╰───╯                                                                │
│   [Floating                                                            │
│     Pill]                                                              │
│                                                                        │
└────────────────────────────────────────────────────────────────────────┘
```

- **Desktop (`>= 768px`):** Fixed hovering vertically along the left viewport margin (`left-6`, centered vertically).
- **Mobile (`< 768px`):** Drops fluidly to the bottom center as an iOS-style floating bottom pill bar.
- **Glassmorphism:** `rounded-full`, `backdrop-blur-2xl`, ultra-fine translucent border (`border border-white/10`).
- **Active Indicator:** Smooth spring capsule behind the active icon with a soft sky-blue glow.

---

## 5. View Specifications

### 5.1 The Home View (Proactive Briefing & Tasks)

The Home view is your peaceful morning deck. When all tasks are cleared, it displays a relaxing zen empty state (*"You're all caught up. Virgil is monitoring in the background."*).

#### Card Archetypes:
1. **Action Ready (Triage & Approvals):**
   - Synthesized from connectors (emails, calendar invites, alerts).
   - Tag: `✦ ACTION READY` in sky blue with `<Sparkles size={13} />`.
   - Title & context preview (e.g. *"Reply drafted for Sarah regarding Q4 budget"*).
   - Actions: Primary pill `[ Review & Send ]` (`<Check />`), ghost pill `[ Dismiss ]` (`<X />`).
   - Bridge: `Discuss with Virgil ↗` (`<ArrowUpRight />`) navigates into Chat with the task context pre-loaded.
2. **Contextual Reminders:**
   - Tag: `GENTLE REMINDER` with `<Clock size={13} />`.
   - Subtitle & preview (e.g. *"Design Sync in 30 mins — prep notes ready"*).
   - Actions: `[ Open Notes ]`, `[ Snooze ]`.
3. **Pre-Configured Routines:**
   - Tag: `PRE-CONFIGURED ROUTINE` with `<Zap size={13} />`.
   - Saved automations ready to launch (e.g. *"Weekly Client Digest"*).
   - Action: `[ Run Now ↗ ]`.

---

### 5.2 The Chat View & Floating Hero Input Pill

A clean, distraction-free conversational canvas:

#### Conversational Stream
- **Width & Spacing:** Centered reading width (`max-w-3xl`) with generous vertical padding.
- **User Messages:** Soft squircle container on the right (`rounded-2xl`, subtle border tint).
- **Virgil Messages:** Unboxed typography on the left heralded by the `✦` spark mark.
- **Thinking Capsules:** When Virgil is scanning tools, it renders an animated, compact frosted capsule:  
  *`<Sparkles size={14} className="text-sky-400 animate-pulse" /> Scanning calendar & inbox...`* with `<ChevronDown size={14} />` to expand steps, eliminating ugly raw terminal dumps.

#### The Floating Hero Input Pill
Suspended in the bottom-center (`bottom-8`, `max-w-2xl w-full`):
```
╭────────────────────────────────────────────────────────────────────────╮
│  (+)  Ask Virgil anything or assign a task...                 (🎙) (↑) │
╰────────────────────────────────────────────────────────────────────────╯
```
- **Left: [ + ] File / Attachment:** Circular button (`<Plus size={18} />`) for documents, images, or code.
- **Center: Auto-Expanding Input:** Clean text input with comforting placeholder text.
- **Right: [ 🎙 ] Audio / Voice:** Circular button (`<Mic size={18} />`). When active, morphs into an animated 5-bar sky-blue audio waveform visualizer.
- **Far Right: [ ↑ ] Send Button:** Circular action button (`<ArrowUp size={18} />`) that illuminates in vibrant sky-blue when message content is present.

---

### 5.3 The Settings Experience

Triggered by tapping `<Settings />` on the hovering dock:
- Opens as a **floating frosted sheet** (`rounded-[32px]`, `backdrop-blur-3xl`, `border border-white/10`) without losing current view context.
- **Theme Switcher:** Two-pill toggle between ☼ Cream & White (`<Sun />`) and ☾ Deep Navy & Sky Blue (`<Moon />`).
- **Device Pairing:** Enter pairing code (`VGL-...`) or view QR code for mobile companion app (`/api/v1/auth/pair`).
- **Telemetry & Health:** Real-time indicator badges for FastAPI backend, Worker queue, and MCP connectors.

---

## 6. Implementation Architecture

When implementing the application inside `web/`:

```
web/src/
├── types/index.ts                  # TaskCard, ChatMessage, ThinkingStep, AppTheme
├── store/useAppStore.ts            # Active tab, theme, tasks, message state
├── services/
│   ├── api.ts                     # REST client for FastAPI backend (/api/v1)
│   ├── sse.ts                     # Server-Sent Events listener for live runs
│   └── mockData.ts                # Standalone simulation data for zero-setup preview
└── components/
    ├── layout/
    │   ├── AmbientBackground.tsx   # Dual-theme radial glow meshes
    │   ├── FloatingSideDock.tsx    # Hovering 3-icon pill dock (Home, Chat, Settings)
    │   └── HeaderBrand.tsx         # Minimalist "Virgil ✦" mark
    ├── home/
    │   ├── BriefingHeader.tsx      # Greeting & zen empty state
    │   ├── TaskCard.tsx            # Frosted squircle action/approval card
    │   ├── ReminderCard.tsx        # Contextual reminder card
    │   └── PreconfiguredCard.tsx   # Routine quick launcher
    ├── chat/
    │   ├── MessageStream.tsx       # Centered conversation history
    │   ├── MessageItem.tsx         # User & agent message formatting
    │   ├── ThinkingCapsule.tsx     # Animated breathing thinking indicator
    │   └── FloatingInputPill.tsx   # Hero floating input island (+, input, mic, send)
    └── settings/
        └── SettingsSheet.tsx       # Floating frosted modal
```

---

## 7. Verification Checklist

- [ ] **Emoji Check:** Zero emojis in UI code. All icons imported from `lucide-react`.
- [ ] **Theme Check:** Deep navy / sky blue in dark mode; warm cream / white in light mode.
- [ ] **Ergonomics Check:** Hovering side dock floats seamlessly; floating input pill responds dynamically.
- [ ] **Dual-Mode Check:** Standalone simulation mode works out-of-the-box with mock tasks and audio waveforms.
