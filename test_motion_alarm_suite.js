/**
 * Comprehensive test suite for Motion Alarm logic
 * Tests:
 * 1. Unit tests for alarm triggering logic
 * 2. Integration tests with Home Assistant events
 * 3. Cooldown functionality (30 seconds)
 * 4. Audio playback invocation & error handling
 * 5. localStorage persistence
 * 6. UI DOM updates (visual banner & red alert theme)
 */

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

function createDomMock(options = {}) {
    const store = new Map(Object.entries(options.localStorage || {}));
    const listeners = {};

    const localStorageMock = {
        getItem: (k) => (store.has(k) ? store.get(k) : null),
        setItem: (k, v) => store.set(k, String(v)),
        removeItem: (k) => store.delete(k),
        clear: () => store.clear(),
        _store: store
    };

    const audioMock = {
        currentTime: 0,
        playCallCount: 0,
        playPromiseReject: false,
        play: function() {
            this.playCallCount++;
            if (this.playPromiseReject) {
                return Promise.reject(new Error('Audio playback failed in test'));
            }
            return Promise.resolve();
        }
    };

    const toggleMock = {
        checked: false,
        listeners: {},
        addEventListener: function(evt, handler) {
            this.listeners[evt] = this.listeners[evt] || [];
            this.listeners[evt].push(handler);
        },
        dispatchChange: function(checkedVal) {
            this.checked = checkedVal;
            const handlers = this.listeners['change'] || [];
            for (const h of handlers) {
                h.call(this);
            }
        }
    };

    const intruderAlertMock = {
        style: { display: 'none' }
    };

    const bodyMock = {
        attributes: {},
        setAttribute: function(name, val) {
            this.attributes[name] = val;
        },
        removeAttribute: function(name) {
            delete this.attributes[name];
        },
        getAttribute: function(name) {
            return this.attributes[name];
        }
    };

    const elements = {
        alarmToggle: toggleMock,
        alarmAudio: audioMock,
        intruderAlert: intruderAlertMock
    };

    const documentMock = {
        body: bodyMock,
        getElementById: function(id) {
            return elements[id] || null;
        },
        addEventListener: function(evt, handler) {
            listeners[evt] = listeners[evt] || [];
            listeners[evt].push(handler);
        },
        _triggerEvent: function(evt) {
            const handlers = listeners[evt] || [];
            for (const h of handlers) {
                h();
            }
        }
    };

    const haSubscriptions = {};
    const haSubscribeMock = function(evtName, callback) {
        haSubscriptions[evtName] = haSubscriptions[evtName] || [];
        haSubscriptions[evtName].push(callback);
    };

    return {
        localStorageMock,
        audioMock,
        toggleMock,
        intruderAlertMock,
        bodyMock,
        documentMock,
        haSubscribeMock,
        haSubscriptions
    };
}

function loadMotionAlarmContext(options = {}) {
    const dom = createDomMock(options);
    const scriptPath = path.join(__dirname, 'static', 'js', 'motion_alarm.js');
    let code = fs.readFileSync(scriptPath, 'utf8');

    // Replace top-level 'let ' with 'var ' so variables attach to context sandbox object
    code = code.replace(/\blet alarmEnabled\b/, 'var alarmEnabled');
    code = code.replace(/\blet lastAlarmTime\b/, 'var lastAlarmTime');

    const context = {
        localStorage: dom.localStorageMock,
        document: dom.documentMock,
        haSubscribe: dom.haSubscribeMock,
        console: {
            log: () => {},
            error: () => {},
            warn: () => {}
        },
        Date: options.Date || Date,
        setTimeout: options.setTimeout || setTimeout,
        clearTimeout: options.clearTimeout || clearTimeout
    };

    vm.createContext(context);
    vm.runInContext(code, context);

    return { dom, context };
}

let testCount = 0;
function runTest(name, fn) {
    testCount++;
    try {
        fn();
        console.log(`PASS: ${name}`);
    } catch (err) {
        console.error(`FAIL: ${name}`);
        throw err;
    }
}

// -------------------------------------------------------------
// Test 1: Unit tests for alarm triggering logic
// -------------------------------------------------------------
runTest('triggerAlarm activates audio, alert banner, and redalert theme', () => {
    const { dom, context } = loadMotionAlarmContext();
    dom.context = context;

    context.alarmEnabled = true;
    context.triggerAlarm();

    assert.strictEqual(dom.audioMock.playCallCount, 1, 'Audio play must be invoked');
    assert.strictEqual(dom.audioMock.currentTime, 0, 'Audio currentTime reset to 0');
    assert.strictEqual(dom.intruderAlertMock.style.display, 'block', 'Banner set to block');
    assert.strictEqual(dom.bodyMock.getAttribute('data-theme'), 'redalert', 'Body given redalert theme');
});

runTest('triggerAlarm handles missing DOM elements gracefully', () => {
    const { dom, context } = loadMotionAlarmContext();
    dom.documentMock.getElementById = () => null;
    dom.documentMock.body = {
        setAttribute: () => {},
        removeAttribute: () => {},
        getAttribute: () => null
    };

    assert.doesNotThrow(() => {
        context.triggerAlarm();
    });
});

runTest('triggerAlarm handles audio play rejection gracefully without throwing', () => {
    const { dom, context } = loadMotionAlarmContext();
    dom.audioMock.playPromiseReject = true;

    assert.doesNotThrow(() => {
        context.triggerAlarm();
    });
    assert.strictEqual(dom.audioMock.playCallCount, 1);
});

// -------------------------------------------------------------
// Test 2: Cooldown functionality
// -------------------------------------------------------------
runTest('triggerAlarm respects 30-second cooldown window', () => {
    let mockCurrentTime = 100000;
    const mockDate = {
        now: () => mockCurrentTime
    };

    const { dom, context } = loadMotionAlarmContext({ Date: mockDate });

    context.alarmEnabled = true;
    context.triggerAlarm();
    assert.strictEqual(dom.audioMock.playCallCount, 1, 'First trigger plays sound');

    // Attempt trigger after 10s (within 30s cooldown)
    mockCurrentTime += 10000;
    context.triggerAlarm();
    assert.strictEqual(dom.audioMock.playCallCount, 1, 'Trigger within 10s skipped due to cooldown');

    // Attempt trigger after 29.999s from first trigger
    mockCurrentTime = 100000 + 29999;
    context.triggerAlarm();
    assert.strictEqual(dom.audioMock.playCallCount, 1, 'Trigger within 29.999s skipped');

    // Attempt trigger after >= 30s cooldown
    mockCurrentTime = 100000 + 30000;
    context.triggerAlarm();
    assert.strictEqual(dom.audioMock.playCallCount, 2, 'Trigger after 30s cooldown succeeds');
});

runTest('cooldown timestamp persists in localStorage on trigger', () => {
    let mockCurrentTime = 1700000000000;
    const mockDate = {
        now: () => mockCurrentTime
    };

    const { dom, context } = loadMotionAlarmContext({ Date: mockDate });
    context.alarmEnabled = true;
    context.triggerAlarm();

    const storedLastTrigger = dom.localStorageMock.getItem('haMotionAlarmLastTrigger');
    assert.strictEqual(storedLastTrigger, String(mockCurrentTime), 'Stored timestamp matches Date.now()');
    assert.strictEqual(context.lastAlarmTime, mockCurrentTime, 'In-memory lastAlarmTime matches');
});

// -------------------------------------------------------------
// Test 3: LocalStorage persistence & initialization
// -------------------------------------------------------------
runTest('initialization loads armed state and last trigger time from localStorage', () => {
    const previousTriggerTime = 1690000000000;
    const { dom, context } = loadMotionAlarmContext({
        localStorage: {
            haMotionAlarmEnabled: 'true',
            haMotionAlarmLastTrigger: String(previousTriggerTime)
        }
    });

    assert.strictEqual(context.alarmEnabled, true, 'alarmEnabled correctly loaded as true');
    assert.strictEqual(context.lastAlarmTime, previousTriggerTime, 'lastAlarmTime correctly parsed as int');

    context.initializeAlarmToggle();
    assert.strictEqual(dom.toggleMock.checked, true, 'Toggle checked reflects true in localStorage');
});

runTest('initialization defaults to disabled and 0 when localStorage empty', () => {
    const { dom, context } = loadMotionAlarmContext({ localStorage: {} });

    assert.strictEqual(context.alarmEnabled, false, 'Default alarmEnabled is false');
    assert.strictEqual(context.lastAlarmTime, 0, 'Default lastAlarmTime is 0');

    context.initializeAlarmToggle();
    assert.strictEqual(dom.toggleMock.checked, false, 'Toggle checked reflects false');
});

runTest('toggle change updates alarmEnabled variable and saves to localStorage', () => {
    const { dom, context } = loadMotionAlarmContext({ localStorage: {} });
    context.initializeAlarmToggle();

    // User switches toggle ON
    dom.toggleMock.dispatchChange(true);
    assert.strictEqual(context.alarmEnabled, true, 'alarmEnabled becomes true on toggle ON');
    assert.strictEqual(dom.localStorageMock.getItem('haMotionAlarmEnabled'), 'true', 'localStorage updated to true');

    // User switches toggle OFF
    dom.toggleMock.dispatchChange(false);
    assert.strictEqual(context.alarmEnabled, false, 'alarmEnabled becomes false on toggle OFF');
    assert.strictEqual(dom.localStorageMock.getItem('haMotionAlarmEnabled'), 'false', 'localStorage updated to false');
});

// -------------------------------------------------------------
// Test 4: Integration with Home Assistant events
// -------------------------------------------------------------
runTest('checkMotionEvents triggers alarm on binary_sensor motion state on when enabled', () => {
    const { dom, context } = loadMotionAlarmContext();
    context.alarmEnabled = true;

    const haEvent = {
        data: {
            entities: {
                'binary_sensor.hallway_motion': { state: 'on' },
                'light.living_room': { state: 'on' }
            }
        }
    };

    context.checkMotionEvents(haEvent);
    assert.strictEqual(dom.audioMock.playCallCount, 1, 'Motion sensor ON triggered alarm');
});

runTest('checkMotionEvents ignores motion events when alarm is disabled', () => {
    const { dom, context } = loadMotionAlarmContext();
    context.alarmEnabled = false;

    const haEvent = {
        data: {
            entities: {
                'binary_sensor.hallway_motion': { state: 'on' }
            }
        }
    };

    context.checkMotionEvents(haEvent);
    assert.strictEqual(dom.audioMock.playCallCount, 0, 'Disabled alarm does not trigger');
});

runTest('checkMotionEvents ignores sensors when state is off or clear', () => {
    const { dom, context } = loadMotionAlarmContext();
    context.alarmEnabled = true;

    const haEvent = {
        data: {
            entities: {
                'binary_sensor.living_room_motion': { state: 'off' },
                'binary_sensor.kitchen_motion': { state: 'clear' }
            }
        }
    };

    context.checkMotionEvents(haEvent);
    assert.strictEqual(dom.audioMock.playCallCount, 0, 'Sensors with state off do not trigger');
});

runTest('checkMotionEvents ignores non-motion sensors even if state is on', () => {
    const { dom, context } = loadMotionAlarmContext();
    context.alarmEnabled = true;

    const haEvent = {
        data: {
            entities: {
                'binary_sensor.front_door_contact': { state: 'on' },
                'sensor.motion_detector_battery': { state: 'on' }
            }
        }
    };

    context.checkMotionEvents(haEvent);
    assert.strictEqual(dom.audioMock.playCallCount, 0, 'Non-motion binary sensors ignored');
});

runTest('DOMContentLoaded listener registers haSubscribe with checkMotionEvents', () => {
    const { dom } = loadMotionAlarmContext();

    // Trigger DOMContentLoaded
    dom.documentMock._triggerEvent('DOMContentLoaded');

    assert.ok(dom.haSubscriptions['state_changed'], 'haSubscribe registered state_changed event');
    assert.strictEqual(dom.haSubscriptions['state_changed'].length, 1);
});

// -------------------------------------------------------------
// Test 5: Timer & auto-cleanup verification (Visual alert & red alert theme)
// -------------------------------------------------------------
runTest('visual alert and redalert theme auto-reset after 5000ms timeout', () => {
    let timerId = 0;
    const scheduledTimeouts = [];
    const mockSetTimeout = (fn, delay) => {
        timerId++;
        scheduledTimeouts.push({ fn, delay, id: timerId });
        return timerId;
    };

    const { dom, context } = loadMotionAlarmContext({ setTimeout: mockSetTimeout });
    context.alarmEnabled = true;
    context.triggerAlarm();

    assert.strictEqual(dom.intruderAlertMock.style.display, 'block');
    assert.strictEqual(dom.bodyMock.getAttribute('data-theme'), 'redalert');

    // 2 timeouts scheduled: 1 for alert banner, 1 for data-theme
    assert.strictEqual(scheduledTimeouts.length, 2);
    assert.strictEqual(scheduledTimeouts[0].delay, 5000);
    assert.strictEqual(scheduledTimeouts[1].delay, 5000);

    // Execute first timeout (banner hide)
    scheduledTimeouts[0].fn();
    assert.strictEqual(dom.intruderAlertMock.style.display, 'none', 'Alert banner hidden after 5s');

    // Execute second timeout (theme reset)
    scheduledTimeouts[1].fn();
    assert.strictEqual(dom.bodyMock.getAttribute('data-theme'), undefined, 'Redalert theme removed after 5s');
});

console.log(`\nALL ${testCount} MOTION ALARM JAVASCRIPT TESTS PASSED.`);
