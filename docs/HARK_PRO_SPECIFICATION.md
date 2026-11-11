# Hark Pro: Definitive UI/UX Design System & Architectural Specification

> **Official Visual & Architectural Reference**: Derived directly from Hark Pro production interface captures.  
> **Target**: Evolve Vigil into an authentic, open-source counterpart to Hark Pro.  
> **Date**: November 2026  

---

## 1. Executive Summary & Core Platform Philosophy

**Hark Pro** (founded by Brett Adcock, Figure AI / Archer Aviation; design directed by former Apple industrial designer Abidur Chowdhury) completely reimagines human-AI computing.

### 1.1 The Central Philosophy: An Operating System, Not a Chatbot
1. **Rejection of the Traditional Multi-Page SaaS Shell**:
   - Hark Pro **completely discards traditional navigation bars, footer bars, and distinct multi-page routes** (`/home`, `/chat`, `/settings`).
   - The entire platform exists as **one unified, ambient living canvas** with instantaneous mode toggles (`[ Chat | Projects ]`), floating controls, and overlay sheets.
2. **Atmospheric Living Backdrop**:
   - The UI does not sit on a flat background or synthetic neon gradient. It floats over an **ambient, photographic cloud/atmospheric simulation** that mirrors daylight and sky tones.
3. **Adaptive Left Column**:
   - The left third of the screen adapts between:
     - **Widgets / Panels View**: Weather card, Usage meter, Calendar card, `+ Create a new panel`.
     - **Typographic Clock View**: A massive, elegant editorial clock (`Tue Nov 10 6:56 PM`).
     - **Projects Index View**: Hierarchical folder/project list when the `Projects` mode is active.
     - **Centralized View**: The left column collapses completely, centering the conversational feed.
4. **Verbs as Primary Actions**:
   - Observation tags embed explicit verb buttons right into the text stream: `[ Accept ] Boardy's 5:30 PM Maji invite... ⌝` or `[ Draft ] a note asking Boardy to move your Tuesday 9 AM... ⌝`.

---

## 2. Visual Anatomy: Screen-by-Screen Breakdown

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│ [⌂]                         [  Chat  |  Projects  ]                 [ 🔍 Search ] (👤) │
├──────────────────────────────┬─────────────────────────────────────────────────────────┤
│                              │                                                         │
│  [ LEFT COLUMN: 3 STATES ]   │                 [ CENTRAL FEED ]                        │
│                              │                                                         │
│  State A: Panels / Widgets   │  Assistant Message Card (frosted squircle)              │
│  ┌────────────────────────┐  │  ┌───────────────────────────────────────────────────┐  │
│  │ Ojota, NG   27°C / 30° │  │  │ • Commits and issue logs reviewed                 │  │
│  │ Sunset 5:25 PM         │  │  │ • Deployment status verified                      │  │
│  └────────────────────────┘  │  └───────────────────────────────────────────────────┘  │
│  ┌────────────────────────┐  │                                                         │
│  │ Usage / Plan Limit     │  │  Proactivity Observation Pill:                          │
│  └────────────────────────┘  │  ┌───────────────────────────────────────────────────┐  │
│  ┌────────────────────────┐  │  │ [ Draft ] a note to Boardy asking to move intro ⌝ │  │
│  │ Calendar Events        │  │  └───────────────────────────────────────────────────┘  │
│  └────────────────────────┘  │                                                         │
│  [ + Create a new panel ]    │  Embedded Cloud Browser Window:                         │
│                              │  ┌───────────────────────────────────────────────────┐  │
│  State B: Typographic Clock  │  │ [ Cloudflare Login Screen / Live Web View       ] │  │
│  Tue                         │  │                                       (● Error) ⛶ │  │
│  Nov 10                      │  │                                                   │  │
│  6:56 PM                     │  └───────────────────────────────────────────────────┘  │
│                              │                                                         │
│  State C: Projects List      │  Structured Action / Draft Card:                        │
│  • FYP                       │  ┌───────────────────────────────────────────────────┐  │
│  • PROJECT IDEAS             │  │ Reply to Boardy and Shubham                     ⛶ │  │
│  • Interview Prep            │  │ To: boardy@boardy.ai   Subject: Re: Intro...      │  │
│                              │  │ [ Full editable draft message body ]              │  │
│                              │  └───────────────────────────────────────────────────┘  │
└──────────────────────────────┴─────────────────────────────────────────────────────────┘
```

---

## 3. The Atmospheric Living Background

The background is a soft, dreamlike, out-of-focus photographic cloud atmosphere that gives Hark Pro its calm luxury aesthetic.

### 3.1 Light Mode Atmosphere ("Warm Sand & Cloud Horizon")
- **Ground Palette**: Blended organic tones of warm sand, ivory peach, alabaster, and soft cloud shadows:
  - Base ground: `#EFE8DE` to `#E3D8CB`
  - Cloud drifts: `#F7F2EA`, `#DECFC0`, `#E8DDD0`, `#D3C3B2`
- **Materiality**: Frosted translucent white card surfaces (`rgba(255, 255, 255, 0.72)`) with a specular top rim highlight (`inset 0 1px 0 rgba(255, 255, 255, 0.85)`), hairline borders (`rgba(0, 0, 0, 0.06)`), and soft layered drop shadows.

### 3.2 Dark Mode Atmosphere ("Dusky Charcoal & Smoky Plum")
- **Ground Palette**: Moody, atmospheric charcoal cloudscape with subtle dusky plum and slate undertones:
  - Base ground: `#131217` to `#1A1820`
  - Cloud drifts: `#24212B`, `#1B1922`, `#2C2836`, `#16141C`
- **Materiality**: Frosted dark obsidian cards (`rgba(35, 33, 42, 0.65)`) with specular glass highlights (`inset 0 1px 0 rgba(255, 255, 255, 0.08)`), subtle borders (`rgba(255, 255, 255, 0.08)`), and deep atmospheric shadows.

---

## 4. Top Floating Chrome & Navigation

Instead of a fixed header or sidebar navigation, Hark Pro uses **floating chrome components anchored at the top of the viewport**:

### 4.1 Home Action Pill (`top-left`)
- Position: Fixed at `top: 20px`, `left: 24px`.
- Shape: Rounded squircle button (`44px × 44px`, `rounded-[14px]`).
- Icon: Minimalist Home mark `[ ⌂ ]`.
- Function: Toggles the left column between the **Widgets/Panels view**, the **Typographic Clock view**, and the **Collapsed Central view**.

### 4.2 Mode Segmented Switch (`top-center`)
- Position: Centered horizontally at `top: 20px`.
- Shape: Floating pill container (`rounded-full`, height `38px`, padding `3px`).
- Options: `[ Chat | Projects ]`.
  - **Active Tab**: High-contrast solid pill (`bg-black text-white` in light mode; `bg-white text-black` in dark mode) with spring transition.
  - **Inactive Tab**: Ghost text (`text-muted`), hover elevates gently.
- Function: Instantly toggles the central workspace between conversational task execution (`Chat`) and structured project file management (`Projects`) with zero page reloads.

### 4.3 Utility Cluster (`top-right`)
- Position: Fixed at `top: 20px`, `right: 24px`.
- **Search Pill**:
  - Pill button (`height: 38px`, `px: 16px`, `rounded-full`).
  - Label: `Search` with subtle magnifying glass icon.
  - Trigger: Clicking opens the **Centered Search Overlay** (blurring the background and popping a focused search pill in the viewport center).
- **User Avatar**:
  - Circular avatar (`38px × 38px`, `rounded-full`, border `1px solid var(--border)`).
  - Trigger: Clicking opens the **User Profile & Settings Sheet**.

---

## 5. The Left Adaptive Column

The left column occupies approximately `300px` to `340px` and dynamically switches across four states:

### State A: The Panels / Widgets View
A vertical stack of pillowy, translucent squircle cards (`rounded-[20px]`, padding `16px–20px`):
1. **Location & Weather Panel**:
   - Location tag: `Ojota, NG` (with GPS pin).
   - Metrics grid: `27°C Overcast Clouds`, `Feels like 30°`, `Sunset 5:25 PM`.
   - Secondary button: `Share your location` (rounded-full).
2. **Usage & Plan Limit Panel**:
   - Header with Hark mark: `Hark Usage`.
   - Status text: `You've reached your plan's limit.`
   - Action button: `Upgrade` (pill button).
3. **Calendar Panel**:
   - Header: `✦ Calendar`.
   - Content: Current agenda or sync state.
4. **Panel Creator Action**:
   - A floating pill button: `+ Create a new panel`.

### State B: The Typographic Clock View
When panels are toggled to clock mode, the widgets collapse into a striking, minimalist typographic display:
```
Tue
Nov 10
6:56 PM
```
- Typography: `Plus Jakarta Sans`, font-weight `400`–`500`, line-height `1.15`, font size `32px`–`42px`.
- Role: Provides a serene, ambient time indicator without cluttered cards.

### State C: The Projects List View (Active when `Projects` is selected)
When the top toggle is set to `Projects`, the left column switches into a clean project navigation list:
- Header action: `New project` (with subtle plus icon).
- Active project: Highlighted with bold typography (e.g. `FYP`).
- Project sub-items: Indented list items with bullets (e.g. `• PROJECT IDEAS`, `• Software Developer Interview Prep`, `Awun`).

### State D: Centralized View
The left column completely retracts with a fluid spring animation, allowing the central conversation and micro-app view to expand to a comfortable, focused width (`max-w-3xl` or `max-w-4xl`).

---

## 6. Proactivity Observation Tags & Verb Buttons

Hark Pro's signature interaction mechanism is the **inline actionable observation tag**:

```
┌────────────────────────────────────────────────────────────────────────┐
│  [ Accept ]  Boardy's 5:30 PM Maji invite for Thursday and...       ⌝  │
└────────────────────────────────────────────────────────────────────────┘
```
and
```
┌────────────────────────────────────────────────────────────────────────┐
│  [ Draft ]  a note asking Boardy to move your Tuesday 9 AM...       ⌝  │
└────────────────────────────────────────────────────────────────────────┘
```

### Visual Specifications
- **Container**: Floating pill shape (`rounded-full`, min-height `44px`, padding `6px 16px 6px 6px`).
- **Surface**:
  - Dark Mode: Translucent slate pill (`rgba(255, 255, 255, 0.08)` with border `rgba(255, 255, 255, 0.12)`).
  - Light Mode: Translucent ivory pill (`rgba(0, 0, 0, 0.05)` with border `rgba(0, 0, 0, 0.08)`).
- **Embedded Verb Button**:
  - Position: Pinned inside the left edge of the pill.
  - Geometry: `rounded-full` or rounded squircle (`rounded-[10px]`), height `32px`, padding `0 14px`.
  - Contrast: Distinct contrasting surface (`rgba(255, 255, 255, 0.15)` in dark mode; `rgba(0, 0, 0, 0.08)` in light mode).
  - Typography: Active verb (`Draft`, `Accept`, `Book`, `Pay`, `RSVP`), font-weight `600`, size `13px`.
- **Contextual Label**:
  - Natural-language observation text with optional sender/service avatar icon (`Boardy`, `Google Calendar`, `GitHub`).
- **Trailing Corner Notch**:
  - A subtle right-angle corner glyph `⌝` at the right end indicating an actionable prompt that deep-links into execution.
- **Interaction Contract**:
  - Clicking the verb button executes the proposed action immediately.
  - Clicking the observation text opens the pre-seeded prompt in the chat input.

---

## 7. Embedded Browser Execution Window

When Hark Pro executes web automation tasks (navigating portals, logging into Cloudflare, booking flights), it does not hide behind a generic loading spinner:

```
┌────────────────────────────────────────────────────────────────────────┐
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │                                                                  │  │
│  │                     [ Live Cloud Browser View ]                  │  │
│  │                     Choose an account                            │  │
│  │                     Sign in with Google                          │  │
│  │                                                                  │  │
│  │                                                      (● Error) ⛶ │  │
│  └──────────────────────────────────────────────────────────────────┘  │
│  On it. I'm signing in to Cloudflare to take down just the             │
│  storefront. The data Worker and backend stay untouched.               │
│  ✓ Cloudflare login saved                                              │
└────────────────────────────────────────────────────────────────────────┘
```

- **Container**: Generous rounded squircle frame (`rounded-[22px]`, background `white` or `surface-solid`).
- **Controls & Status**:
  - Floating status badge in lower right: `● Error` (pill in red `#DC2626`) or `● Active` (pill in green).
  - Fullscreen / Pop-out icon `⛶` to expand the browser view.
- **Accompanying Telemetry**: The assistant's natural-language status update and structured success checkmarks (`✓ Cloudflare login saved`) render immediately below the browser window.

---

## 8. Structured Draft & Summary Artifact Cards

### 8.1 The Email / Communication Card
When drafting or editing communications, Hark renders an editable structured card:
- Header: `Reply to Boardy and Shubham` (with pop-out / expand icon `⛶` on top-right).
- Metadata block:
  - `To: boardy@boardy.ai, shubhammaurya2385@gmail.com`
  - `Subject: Re: Boardy Intro: Nasir + Shubham`
- Body: Clean typography in an indented container.
- Prompt follow-up: Below the card, the assistant asks for user authorization before sending:
  > *"This replies on the Shubham thread, so he sees it too. Should I send it as written, or would you rather it go to Boardy only?"*

### 8.2 The Document Summary Sheet
When reviewing multi-file repositories or large deliverables, Hark opens an editorial document sheet:
- Header: Floating title `Summary` with minimize `⤢` and action `...` buttons.
- Document title: Elegant editorial serif typography (`vensonline-backend, Oct 17 to 19`).
- Content: Clean markdown with styled code pills (`router`, `service`, `dependencies.py`).

---

## 9. Centered Overlay Sheets (Search & User Settings)

### 9.1 The Centered Search Overlay
Clicking the `Search` pill blurs the entire atmospheric backdrop and presents a single, focused floating input bar:
- Geometry: `rounded-full`, height `48px`, max-width `540px`, backdrop blur `40px`.
- Icon: Clean vector magnifying glass.
- Placeholder: `Search`.

### 9.2 The User Profile & Settings Sheet
Clicking the user avatar displays a macOS/iOS-style grouped settings sheet floating in the center:
- **Header**: User avatar, full name (`Nasir Dauda`), email (`nasirdauda2015@gmail.com`), and quick search icon.
- **Tier 1 Card (Subscription)**: `hark pro`, `[ Upgrade ]` button, `Usage and plan >`.
- **Tier 2 Card (User Management)**:
  - `👤 Profile >`
  - `⏱ Scheduled tasks >`
  - `🔗 Accounts >`
  - `💳 Wallet >`
- **Tier 3 Card (System & Security)**:
  - `⚙ System Preferences >`
  - `🛡 Security >`
  - `💬 Submit Feedback >`
  - `ⓘ About >`
- **Tier 4 Card**: `↪ Sign Out`.
- **Dismiss**: Circular close button `[ ✕ ]` in the top right.

---

## 10. Implementation Mapping for Vigil

To implement this exact Hark Pro interface in Vigil:

1. **Canvas Architecture (`web/src/App.tsx` & `web/src/components/layout/AppShell.tsx`)**:
   - Replace separate route views with **one unified living canvas**.
   - Mount `AmbientAtmosphere.tsx` rendering the living cloud backdrop (light mode sand/cream, dark mode charcoal/plum).
   - Render the floating top chrome: Home button `[ ⌂ ]`, segmented switch `[ Chat | Projects ]`, `[ Search ]`, and user avatar.
2. **Left Adaptive Column (`web/src/components/home/LeftRail.tsx`)**:
   - Support four display modes: `panels`, `clock`, `projects`, and `collapsed`.
   - Implement the Typographic Clock (`Tue Nov 10 6:56 PM`).
   - Implement the Panels stack (Weather widget, Usage widget, Calendar widget, `+ Create a new panel`).
3. **Proactivity Tags & Verb Buttons (`web/src/components/chat/ProactivityPill.tsx`)**:
   - Build the exact Hark Pro pill: `[ [Verb]  Contextual Observation... ⌝ ]`.
   - Wire single-tap execution for the verb button.
4. **Cloud Browser Live Stream (`web/src/components/chat/LiveBrowserCard.tsx`)**:
   - Embed the browser execution window into the chat thread with status indicators (`● Error`, `● Running`) and expand toggles.
5. **Overlay Sheets (`web/src/components/modals/`)**:
   - Implement the centered floating Search overlay and the grouped Profile/Settings sheet.
