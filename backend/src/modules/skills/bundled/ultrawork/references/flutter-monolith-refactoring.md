# Flutter Monolith Refactoring with Ultrawork

**Session:** LunaCycle period tracker — 4362-line `app.dart` split into 8 files
**Date:** 2026-05-06
**Pattern:** 3 waves × 3 subagents + 1 sequential rewrite + integration fix-up

## Why Flutter Refactoring Is Different From React

| Aspect | React/TS Monolith | Flutter/Dart Monolith |
|--------|-------------------|----------------------|
| Decomposition target | Hooks + components + pages | Screen widgets + shared widgets |
| Import paths | `./` or `../` from any depth | Relative to file: `../` vs `../../` matters |
| State management | Context/Redux + hooks | InheritedWidget / stateful widget + callbacks |
| Parallel extraction risk | Few duplicates (hooks vs components) | High (subagents extract same widget classes) |
| Shell rewrite loss risk | Low (routing + providers only) | High (onboarding flow, lifecycle callbacks) |

## Wave Planning (Flutter)

### Wave 1: Screen skeletons (3 tasks)
Extract screens that are naturally independent. Each subagent reads a line range from the monolith and writes a standalone file.

| Screen | Source Lines | Key Dependencies |
|--------|-------------|-----------------|
| OnboardingScreen | 124-527 | AppColors, date_utils, SharedWidgets |
| TodayScreen + CycleRing | 707-1366 | CyclePrediction, AppColors, SharedWidgets |
| CalendarScreen + CalendarDay | 1368-1583, 3587-3675 | CyclePrediction, SharedWidgets |

### Wave 2: More screens (3 tasks)

| Screen | Source Lines | Key Dependencies |
|--------|-------------|-----------------|
| DailyLogScreen | 1585-1989 | CyclePrediction, AppColors, number_utils, SharedWidgets |
| InsightsScreen + TrendChart | 1991-2139, 3767-3823 | CyclePrediction, text_utils, SharedWidgets |
| SelfCareScreen + ReminderCard | 2141-2487, 3677-3765 | DeviceServices, CyclePrediction, SharedWidgets |

### Wave 3: Settings + shared widgets + shell (3 tasks)

| Component | Source Lines | Key Dependencies |
|-----------|-------------|-----------------|
| SettingsScreen + info screens | 2489-3044 | DeviceServices, AppColorScheme, SharedWidgets |
| SharedWidgets (22 classes) | 3046-4383 | AppColors, material.dart |
| App shell rewrite | 1-123, 526-705 | All of the above |

## Import Path Pitfall (Flutter-Specific)

The original monolith lives at `lib/presentation/app.dart`. When you move classes to
`lib/presentation/screens/*.dart`, the import paths change:

```
# In app.dart (original):
import '../core/theme/app_colors.dart'          # → lib/core/theme/app_colors.dart ✓

# In screens/today_screen.dart (WRONG):
import '../core/theme/app_colors.dart'          # → lib/presentation/core/... ✗

# In screens/today_screen.dart (RIGHT):
import '../../core/theme/app_colors.dart'       # → lib/core/theme/app_colors.dart ✓

# In widgets/shared_widgets.dart:
import '../../core/theme/app_colors.dart'       # → lib/core/theme/app_colors.dart ✓
```

**Fix pattern:** After all waves, run `flutter analyze` and batch-fix wrong imports:
```bash
cd lib/presentation/screens
sed -i "s|import '../core/|import '../../core/|g" *.dart
sed -i "s|import '../data_model/|import '../../data_model/|g" *.dart
```

## Duplicate Class Resolution

When subagents extract overlapping code, you get duplicate class definitions.
Check for these after all waves:

```bash
# Find duplicates — any class defined in two files
grep -rn "^class " lib/presentation/ | awk '{print $2}' | sort | uniq -d
```

**Common Flutter duplicates from overlapping extraction:**
- `CalendarDay` — extracted into both `calendar_screen.dart` and `shared_widgets.dart`
- `ReminderCard` — extracted into both `self_care_screen.dart` and `shared_widgets.dart`
- `TrendChart` / `TrendChartPainter` — extracted into both `insights_screen.dart` and `shared_widgets.dart`
- `SafeSheet` — extracted into both `onboarding_screen.dart` and `settings_screen.dart`

**Fix:** Keep the class in its natural home (screen file) and remove from shared widgets.
Use `patch(old_string=..., new_string='')` to delete duplicate blocks.

## Shell Rewrite: Don't Lose the Logic

The app shell (`app.dart` after refactoring) must retain:
- The **onboarding flow** — checks `_state.name.isEmpty` and routes to `OnboardingScreen`
- The **privacy lock lifecycle** — `WidgetsBindingObserver`, `didChangeAppLifecycleState`
- The **palette callback** — propagates `selectedPalette` changes up to `PeriodTrackerApp`
- The **reminder sync** — `_shouldSyncReminders()` and `DeviceServices.scheduleReminders()`
- The **5-tab shell** — `MainShell` with `TodayScreen`, `CalendarScreen`, `DailyLogScreen`, `InsightsScreen`, `SelfCareScreen`

**Verification after shell rewrite:**
```dart
// These should all be present in the shell:
// 1. PeriodTrackerApp (stateful, holds palette)
// 2. _PeriodTrackerAppState._onPaletteChanged
// 3. LunaCycleRoot (with onPaletteChanged callback)
// 4. _LunaCycleRootState._loadState → widget.onPaletteChanged
// 5. _LunaCycleRootState.build → if (name.empty) OnboardingScreen else MainShell
// 6. MainShell + _MainShellState (5 tabs, privacy lock, log focus)
```

## Post-Wave Integration

1. Run `flutter analyze` — fix import paths first (batch with sed)
2. Run `flutter analyze` again — fix duplicate classes (patch to delete)
3. Run `flutter analyze` a third time — fix any remaining issues
4. Run `flutter test` — verify all tests pass
5. Verify the app starts and navigates correctly
