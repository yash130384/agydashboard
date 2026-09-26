# LCARS Motion Alarm Feature Documentation

## Overview
Home Assistant motion sensor alert integrated into the LCARS dashboard. Triggers Rogue One alert audio and visual red alert banner upon motion detection when armed.

## Components
- `static/js/motion_alarm.js`: Core client-side logic, event listener, state management, and cooldown timer.
- `static/audio/rogue_one_alarm.mp3`: Alert klaxon audio file.
- `templates/base.html`: LCARS toggle switch, status displays, event log, audio tag, and red alert banner.

## Usage & Behavior
- **Toggle switch**: Arms/disarms motion alarm (`#alarmToggle`).
- **Persistence**: Arm status stored in `localStorage` under key `haMotionAlarmEnabled`.
- **Cooldown**: 30-second cooldown (`haMotionAlarmLastTrigger`) prevents repeated triggering.
- **Home Assistant integration**: Subscribes via `haSubscribe('state_changed', checkMotionEvents)` watching `binary_sensor.*motion*` entities for state `'on'`.
- **Visual alert**: Displays red alert banner and applies `data-theme="redalert"` to body for 5 seconds.
