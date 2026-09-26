#!/usr/bin/env python3
"""
Wiimote -> MIDI CC bridge for MODEP / mod-ui.

Reads the Wiimote's accelerometer AND buttons concurrently via evdev
(using selectors, since both are separate blocking device nodes),
smooths/deadzones the accel data, and sends MIDI CC out over a virtual
ALSA MIDI port (via mido + python-rtmidi) that you then patch into
MODEP's "Virtual MIDI Loopback" input with `aconnect`.

Install deps:
    pip3 install evdev mido python-rtmidi

Find your device paths first with `evtest` (pin them via
/dev/input/by-id/ so they're stable across reboots):
    ls -la /dev/input/by-id/ | grep -i wii
"""

import selectors
import mido
from evdev import InputDevice, ecodes

# --- CONFIGURE THESE -----------------------------------------------------
ACCEL_DEVICE_PATH = "/dev/input/by-id/xxxx-event-accelerometer"  # from evtest
BUTTON_DEVICE_PATH = "/dev/input/by-id/xxxx-event-joystick"      # from evtest
MIDI_PORT_NAME = "WiimoteBridge"
MIDI_CHANNEL = 0            # channel 1
CC_TILT_X = 20              # e.g. headstock tilt -> whammy-style CC
CC_TILT_Y = 21              # secondary axis, e.g. wah/filter
DEADZONE = 6                # ignore jitter smaller than this (raw accel units)
SMOOTHING = 0.35            # 0 = no smoothing, closer to 1 = heavier smoothing
ACCEL_MIN, ACCEL_MAX = 300, 730   # calibrate: rest value ~512, swing range varies
# ---------------------------------------------------------------------------

BUTTON_CC_MAP = {
    ecodes.BTN_A: 22,
    ecodes.BTN_B: 23,
    ecodes.BTN_1: 24,
    ecodes.BTN_2: 25,
}

AXIS_CODES = {
    ecodes.ABS_RX: "x",
    ecodes.ABS_RY: "y",
}


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def scale_to_midi(raw, lo, hi):
    span = hi - lo
    if span <= 0:
        return 64
    pct = clamp((raw - lo) / span, 0.0, 1.0)
    return int(round(pct * 127))


def handle_accel_event(event, smoothed, last_raw, last_midi, outport):
    if event.type != ecodes.EV_ABS or event.code not in AXIS_CODES:
        return

    axis = AXIS_CODES[event.code]
    raw = event.value

    # exponential smoothing to kill high-frequency jitter
    smoothed[axis] = (SMOOTHING * smoothed[axis]) + ((1 - SMOOTHING) * raw)

    # deadzone compares against the last raw value we actually *acted on*,
    # not a default that fakes a zero delta on the first sample
    if last_raw[axis] is not None and abs(smoothed[axis] - last_raw[axis]) < DEADZONE:
        return
    last_raw[axis] = smoothed[axis]

    midi_val = scale_to_midi(smoothed[axis], ACCEL_MIN, ACCEL_MAX)
    if midi_val == last_midi[axis]:
        return
    last_midi[axis] = midi_val

    cc = CC_TILT_X if axis == "x" else CC_TILT_Y
    outport.send(mido.Message('control_change', channel=MIDI_CHANNEL,
                               control=cc, value=midi_val))


def handle_button_event(event, outport):
    if event.type != ecodes.EV_KEY or event.code not in BUTTON_CC_MAP:
        return
    # value: 1 = press, 0 = release, 2 = autorepeat (ignore repeats)
    if event.value == 2:
        return
    cc = BUTTON_CC_MAP[event.code]
    midi_val = 127 if event.value == 1 else 0
    outport.send(mido.Message('control_change', channel=MIDI_CHANNEL,
                               control=cc, value=midi_val))


def main():
    accel_dev = InputDevice(ACCEL_DEVICE_PATH)
    try:
        button_dev = InputDevice(BUTTON_DEVICE_PATH)
    except FileNotFoundError:
        button_dev = None
        print("Button device not found, continuing with accel only.")

    outport = mido.open_output(MIDI_PORT_NAME, virtual=True)
    print(f"MIDI virtual port '{MIDI_PORT_NAME}' open. "
          f"Connect it with: aconnect '{MIDI_PORT_NAME}' 'Virtual MIDI Loopback'")

    smoothed = {"x": 512.0, "y": 512.0}
    last_raw = {"x": None, "y": None}
    last_midi = {"x": -1, "y": -1}

    sel = selectors.DefaultSelector()
    sel.register(accel_dev, selectors.EVENT_READ, data="accel")
    if button_dev:
        sel.register(button_dev, selectors.EVENT_READ, data="button")

    accel_dev.grab()  # comment out if you want the OS to also see events

    try:
        while True:
            for key, _mask in sel.select():
                dev = key.fileobj
                for event in dev.read():
                    if key.data == "accel":
                        handle_accel_event(event, smoothed, last_raw, last_midi, outport)
                    else:
                        handle_button_event(event, outport)
    except KeyboardInterrupt:
        print("Stopping.")
    finally:
        accel_dev.ungrab()
        outport.close()


if __name__ == "__main__":
    main()
