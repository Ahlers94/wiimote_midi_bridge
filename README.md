# Wiimote → MIDI Bridge (for MODEP / mod-ui)

Turn a Wii Remote into a wireless MIDI controller for your pedalboard. Band it to your guitar's headstock, and its tilt/motion drives continuous effect parameters (whammy, wah, filter sweep, whatever you map it to) while the A/B/1/2 buttons toggle effects on and off.

Built to run on a Raspberry Pi 4 running [MODEP](https://github.com/blokas/modep) (or any mod-ui setup).

## How it works

```
Wiimote (Bluetooth)
   └─ evdev device nodes (accelerometer + buttons)
        └─ wiimote_midi_bridge.py
             ├─ smooths + deadzones accel data
             └─ sends MIDI CC
                  └─ virtual ALSA MIDI port ("WiimoteBridge")
                       └─ aconnect → MODEP's "Virtual MIDI Loopback"
                            └─ mod-ui pedalboard, CCs mapped to plugin params
```

The script listens to two separate input device nodes at once (accelerometer and buttons) using `selectors`, since they're distinct evdev devices. Accelerometer values are smoothed and deadzoned before being scaled to 0–127 and sent as MIDI CC. Buttons send CC 127/0 on press/release.

## Hardware

- Raspberry Pi 4 (or any Linux box) running MODEP / mod-ui
- Wii Remote (original or Wii Remote Plus — original tested; Plus may need a slightly different driver path)
- A way to band/strap the Wiimote to your headstock
- Bluetooth on the Pi (built-in on the Pi 4 works fine)

## Setup

### 1. Pair the Wiimote over Bluetooth

Make sure the `hid-wiimote` kernel driver is available (it's built into most distro kernels since Linux 3.3 — no action usually needed) and that your BlueZ version includes the wiimote plugin (standard since bluez-4.96). Without `hid-wiimote` loaded, the Wiimote will still pair as a generic Bluetooth HID device, but it won't be usable — there's no protocol parser to turn its reports into evdev events.

Put the Wiimote into discoverable mode using the **red sync button behind the battery cover** — it's more reliable than the 1+2-button method and, importantly, pairing this way is what enables auto-reconnect (so the Wiimote reconnects on its own when you press any button later, instead of needing to be manually re-paired every time it sleeps). It stays discoverable for about 20 seconds, so have `bluetoothctl` ready:

```bash
sudo bluetoothctl
# inside bluetoothctl:
scan on
# press the sync button under the battery cover, watch for "Nintendo RVL-CNT-01"
pair <MAC_ADDRESS>
trust <MAC_ADDRESS>
connect <MAC_ADDRESS>
```

This is mostly standard Bluetooth pairing, with one wrinkle: if `bluetoothctl` prompts you for a PIN during pairing, that means your `bluetoothd` doesn't have the wiimote plugin, and PIN-based bonding won't work with the Wiimote. In that case, skip pairing/bonding entirely and just connect directly — no PIN needed.

Once connected, check `dmesg` for `hid-wiimote` picking up the device, and confirm the evdev nodes show up under `/dev/input/by-id/`.

### 2. Install dependencies

```bash
pip3 install evdev mido python-rtmidi
```

### 3. Find your device paths

```bash
ls -la /dev/input/by-id/ | grep -i wii
```

You should see something like:
- `...-event-joystick` → buttons
- `...-event-accelerometer` → accelerometer/gyro axes (ABS_RX / ABS_RY)

Use `evtest` to confirm which is which and to sanity-check that moving the Wiimote actually produces `EV_ABS` events:

```bash
sudo evtest
```

**Use `/dev/input/by-id/` paths, not `/dev/input/eventN`** — the `eventN` numbering isn't stable across reboots/reconnects, but the by-id symlinks are.

### 4. Configure the script

Open `wiimote_midi_bridge.py` and edit the block at the top:

| Variable | What it does |
|---|---|
| `ACCEL_DEVICE_PATH` / `BUTTON_DEVICE_PATH` | Paths from step 3 |
| `MIDI_PORT_NAME` | Name of the virtual ALSA MIDI port that gets created |
| `MIDI_CHANNEL` | MIDI channel (0-indexed, so `0` = channel 1) |
| `CC_TILT_X` / `CC_TILT_Y` | CC numbers sent for the two tilt axes |
| `DEADZONE` | Minimum change (raw units) before a new value is sent — filters jitter |
| `SMOOTHING` | 0 = no smoothing, closer to 1 = heavier/laggier smoothing |
| `ACCEL_MIN` / `ACCEL_MAX` | Calibration range — raw accel values mapping to MIDI 0–127 |

`BUTTON_CC_MAP` near the top of the file controls which button sends which CC:

| Button | Default CC |
|---|---|
| A | 22 |
| B | 23 |
| 1 | 24 |
| 2 | 25 |

Tilt axes default to CC 20 (X) and CC 21 (Y).

### 5. Calibrate `ACCEL_MIN` / `ACCEL_MAX`

Run the script once with `evtest` open on the accel device (or add a temporary `print()` in `handle_accel_event`) and note the raw values at rest and at the extremes of the tilt range you'll actually use while playing. Set `ACCEL_MIN`/`ACCEL_MAX` to that observed range — not the theoretical sensor range — so the full 0–127 MIDI sweep matches your actual playing motion.

### 6. Run it

```bash
python3 wiimote_midi_bridge.py
```

You should see:

```
MIDI virtual port 'WiimoteBridge' open. Connect it with: aconnect 'WiimoteBridge' 'Virtual MIDI Loopback'
```

### 7. Patch into MODEP

```bash
aconnect 'WiimoteBridge' 'Virtual MIDI Loopback'
```

Then in mod-ui, MIDI-learn the parameters you want controlled (right-click a knob → MIDI Learn, then wiggle the Wiimote or hit a button) and they'll pick up the incoming CCs.

## Mounting on the headstock

Any of the usual elastic/velcro strap-mount tricks people use for phone gimbals or old expression-pedal hacks work fine — just make sure the Wiimote is oriented consistently every time you strap it on, since `ACCEL_MIN`/`ACCEL_MAX` calibration assumes a fixed orientation relative to your tilt motion.

## Troubleshooting

- **`FileNotFoundError` on the accel/button device** — Bluetooth didn't reconnect, or the `by-id` symlink changed. Re-check `ls /dev/input/by-id/`.
- **Permission denied opening `/dev/input/...`** — your user isn't in the `input` group. `sudo usermod -aG input $USER`, then log out/in.
- **No events at all in `evtest`** — the Wiimote may have gone to sleep (it powers off after a few minutes idle with no traffic); reconnect it, or send periodic rumble/LED commands to keep it awake if this becomes an issue mid-set.
- **Wiimote disconnects randomly** — Bluetooth range/interference; keep the Pi reasonably close, and consider `trust`-ing it (done in step 1) so it auto-reconnects.
- **Jittery/noisy CC values** — increase `DEADZONE` and/or `SMOOTHING`.
- **Values pinned at 0 or 127 the whole time** — your `ACCEL_MIN`/`ACCEL_MAX` calibration is off; redo step 5.

## Running on boot (optional)

Once it's confirmed working, wrap it in a systemd unit so it starts automatically after the Pi boots and Bluetooth reconnects — happy to add a sample `.service` file here once the script's been tested live.

## License

Standard MIT License
