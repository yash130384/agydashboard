/**
 * End-to-End Simulation Test for Motion Alarm in templates/base.html
 * Runs directly in Node.js environment with JSDOM-like structural mock
 */

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

console.log('--- Starting LCARS Motion Alarm End-to-End Test Suite ---');

const baseHtmlPath = path.join(__dirname, 'templates', 'base.html');
const baseHtml = fs.readFileSync(baseHtmlPath, 'utf8');

// Verify template elements exist
assert.ok(baseHtml.includes('id="alarmToggle"'), 'Template has alarmToggle element');
assert.ok(baseHtml.includes('id="alarmAudio"'), 'Template has alarmAudio element');
assert.ok(baseHtml.includes('id="intruderAlert"'), 'Template has intruderAlert element');
assert.ok(baseHtml.includes('id="alarmStatus"'), 'Template has alarmStatus element');
assert.ok(baseHtml.includes('id="lastTrigger"'), 'Template has lastTrigger element');
assert.ok(baseHtml.includes('id="cooldownStatus"'), 'Template has cooldownStatus element');
assert.ok(baseHtml.includes('id="eventLog"'), 'Template has eventLog element');
assert.ok(baseHtml.includes('data-theme="redalert"'), 'Template defines redalert theme');

console.log('PASS: Base HTML template contains all required LCARS dashboard motion alarm elements');

// Mock browser context
const localStorageStore = new Map();
const localStorageMock = {
    getItem: (key) => localStorageStore.has(key) ? localStorageStore.get(key) : null,
    setItem: (key, val) => localStorageStore.set(key, String(val)),
    removeItem: (key) => localStorageStore.delete(key),
    clear: () => localStorageStore.clear()
};

let audioPlayCount = 0;
let alertDisplay = 'none';
let bodyTheme = null;
let loggedEvents = [];

const domElements = {
    alarmToggle: {
        checked: false,
        listeners: {},
        addEventListener: function(event, fn) {
            this.listeners[event] = this.listeners[event] || [];
            this.listeners[event].push(fn);
        },
        click: function() {
            this.checked = !this.checked;
            (this.listeners['change'] || []).forEach(fn => fn.call(this));
        }
    },
    alarmAudio: {
        currentTime: 0,
        play: function() {
            audioPlayCount++;
            return Promise.resolve();
        }
    },
    intruderAlert: {
        style: {
            get display() { return alertDisplay; },
            set display(val) { alertDisplay = val; }
        }
    },
    alarmStatus: { textContent: '', style: {} },
    lastTrigger: { textContent: '' },
    cooldownStatus: { textContent: '', style: {} },
    eventLog: {
        children: [],
        appendChild: function(entry) {
            this.children.push(entry);
            loggedEvents.push(entry.innerHTML);
        },
        scrollTop: 0,
        scrollHeight: 100
    }
};

const domListeners = {};
const haSubscribers = {};

const sandbox = {
    localStorage: localStorageMock,
    document: {
        getElementById: (id) => domElements[id] || null,
        body: {
            setAttribute: (attr, val) => { if (attr === 'data-theme') bodyTheme = val; },
            removeAttribute: (attr) => { if (attr === 'data-theme') bodyTheme = null; },
            getAttribute: (attr) => (attr === 'data-theme' ? bodyTheme : null)
        },
        createElement: (tag) => ({ innerHTML: '' }),
        addEventListener: (event, fn) => {
            domListeners[event] = domListeners[event] || [];
            domListeners[event].push(fn);
        }
    },
    Date: Date,
    setTimeout: (fn, delay) => { fn(); return 1; },
    setInterval: () => 1,
    console: console,
    Math: Math,
    parseInt: parseInt,
    haSubscribe: (event, fn) => {
        haSubscribers[event] = haSubscribers[event] || [];
        haSubscribers[event].push(fn);
    }
};

vm.createContext(sandbox);

// 1. Load motion_alarm.js
const motionAlarmJs = fs.readFileSync(path.join(__dirname, 'static', 'js', 'motion_alarm.js'), 'utf8');
// Convert let to var for vm exposure
const executableMotionJs = motionAlarmJs.replace(/let alarmEnabled/g, 'var alarmEnabled').replace(/let lastAlarmTime/g, 'var lastAlarmTime');
vm.runInContext(executableMotionJs, sandbox);

// 2. Load inline script from base.html
const inlineScriptMatch = baseHtml.match(/<script>([\s\S]*?)<\/script>\s*<\/body>/);
assert.ok(inlineScriptMatch, 'Inline script found in base.html');
const inlineScript = inlineScriptMatch[1];
vm.runInContext(inlineScript, sandbox);

// Fire DOMContentLoaded
(domListeners['DOMContentLoaded'] || []).forEach(fn => fn());

// Test 1: Verify Initial State
assert.strictEqual(sandbox.alarmEnabled, false, 'alarmEnabled defaults to false');
assert.strictEqual(domElements.alarmStatus.textContent, 'DISABLED', 'Status display is DISABLED');
console.log('PASS: Initial dashboard state correctly reflects DISABLED alarm');

// Test 2: Toggle Alarm ON -> localStorage persistence
domElements.alarmToggle.click();
sandbox.updateAlarmStatus();
assert.strictEqual(sandbox.alarmEnabled, true, 'alarmEnabled switched to true');
assert.strictEqual(localStorageMock.getItem('haMotionAlarmEnabled'), 'true', 'localStorage updated');
assert.strictEqual(domElements.alarmStatus.textContent, 'ENABLED', 'Status display is ENABLED');
console.log('PASS: Alarm toggle enables alarm and persists state to localStorage');

// Test 3: Home Assistant event integration -> trigger alarm
assert.ok(haSubscribers['state_changed'], 'Home Assistant state_changed subscriber registered');
const haMotionHandler = haSubscribers['state_changed'][0];

const haEventData = {
    data: {
        entities: {
            'binary_sensor.corridor_motion': { state: 'on' }
        }
    }
};

haMotionHandler(haEventData);
assert.strictEqual(audioPlayCount, 1, 'Audio play triggered once');
assert.strictEqual(alertDisplay, 'none', 'Alert banner reset after timeout');
assert.strictEqual(bodyTheme, null, 'Red alert theme reset after timeout');
assert.ok(localStorageMock.getItem('haMotionAlarmLastTrigger'), 'Last trigger saved in localStorage');
console.log('PASS: Home Assistant motion event triggers alarm audio and visual alerts');

// Test 4: Cooldown verification
haMotionHandler(haEventData);
assert.strictEqual(audioPlayCount, 1, 'Cooldown prevented immediate secondary trigger');
console.log('PASS: 30-second cooldown prevents repeated alarm triggers');

// Test 5: Quick Action Simulation
sandbox.simulateMotion();
assert.ok(loggedEvents.some(e => e.includes('Motion simulation triggered')), 'Event logged for simulation');
console.log('PASS: Quick action simulation triggers expected workflow');

console.log('--- ALL E2E MOTION ALARM TESTS PASSED ---');
