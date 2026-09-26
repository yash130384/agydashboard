// Alarm state management
let alarmEnabled = localStorage.getItem('haMotionAlarmEnabled') === 'true';
let lastAlarmTime = parseInt(localStorage.getItem('haMotionAlarmLastTrigger')) || 0;
const ALARM_COOLDOWN_MS = 30000; // 30 seconds cooldown

// Initialize alarm toggle button
function initializeAlarmToggle() {
    const toggle = document.getElementById('alarmToggle');
    if (toggle) {
        toggle.checked = alarmEnabled;
        toggle.addEventListener('change', function() {
            alarmEnabled = this.checked;
            localStorage.setItem('haMotionAlarmEnabled', alarmEnabled.toString());
        });
    }
}

// Play alarm sound and show visual alert
function triggerAlarm() {
    const now = Date.now();
    if (now - lastAlarmTime < ALARM_COOLDOWN_MS) {
        console.log('Alarm cooldown active, skipping trigger');
        return;
    }
    
    lastAlarmTime = now;
    localStorage.setItem('haMotionAlarmLastTrigger', lastAlarmTime.toString());
    
    // Play alarm sound
    const audio = document.getElementById('alarmAudio');
    if (audio) {
        audio.currentTime = 0;
        audio.play().catch(e => console.error('Audio play error:', e));
    }
    
    // Show visual alert
    const alert = document.getElementById('intruderAlert');
    if (alert) {
        alert.style.display = 'block';
        if (typeof setTimeout === 'function') {
            setTimeout(() => {
                alert.style.display = 'none';
            }, 5000); // Hide after 5 seconds
        }
    }
    
    // Trigger red alert theme
    document.body.setAttribute('data-theme', 'redalert');
    if (typeof setTimeout === 'function') {
        setTimeout(() => {
            document.body.removeAttribute('data-theme');
        }, 5000);
    }
}

// Check for motion sensor events
function checkMotionEvents(eventData) {
    if (!alarmEnabled) return;
    
    // Check if any binary sensor has state 'on'
    const sensors = eventData.data?.entities || {};
    for (const entityId in sensors) {
        if (entityId.includes('binary_sensor') && 
            entityId.includes('motion') && 
            sensors[entityId]?.state === 'on') {
            triggerAlarm();
            break;
        }
    }
}

// Initialize when DOM is loaded
document.addEventListener('DOMContentLoaded', function() {
    initializeAlarmToggle();
    
    // Subscribe to Home Assistant events if available
    if (typeof haSubscribe === 'function') {
        haSubscribe('state_changed', checkMotionEvents);
    }
});