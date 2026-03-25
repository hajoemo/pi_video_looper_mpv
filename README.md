# mpv_video_looper

A drop-in spiritual successor to [adafruit/pi_video_looper](https://github.com/adafruit/pi_video_looper), rebuilt around **mpv** as the player backend. Works on current Raspberry Pi OS (Bookworm, Bullseye) and supports the Pi 4 and Pi 5 without requiring legacy OS images.

---

## Why mpv?

| Feature | pi_video_looper (omxplayer) | mpv_video_looper (mpv) |
|---|---|---|
| Supported OS | Legacy (Buster) only | Bookworm / Bullseye / current |
| Pi 5 support | Doubtful | ✅ |
| Hardware decode | OMX (deprecated) | `--hwdec=auto` (V4L2/MMAL/VAAPI) |
| Audio codecs | Limited | All ffmpeg codecs |
| Video codecs | H264/MPEG2/VC1 | Virtually everything |
| Gapless loop (single file) | ✅ | ✅ (`--loop`) |
| Active upstream | ❌ Abandoned | ✅ |

---

## Features

- **USB drive auto-mount** – plug in a drive and videos play automatically
- **Directory mode** – watch a local folder; playlist rebuilds when files change
- **Copy-mode** – copy files from USB to SD card with a progress OSD; password-protected
- **M3U playlist support** – fixed playlists with optional `#EXTINF` titles
- **`_repeat_Nx` filename tag** – e.g. `intro_repeat_3x.mp4` plays 3 times before continuing
- **Random / random-unique playback**
- **Resume last position** across reboots
- **One-shot playback** – stop after each file, wait for a trigger
- **Configurable wait time** between files
- **Date/time OSD** shown between videos
- **Background image or solid color** between videos
- **Title overlay** during playback (from filename or M3U `#EXTINF`)
- **Keyboard control** (ESC, k, b, s, p, Space, o, i)
- **GPIO control** – map RPi GPIO pins to playlist jumps, filenames, or keyboard commands
- **Image slideshow mode** – display images instead of videos
- **Supervisor-managed** – auto-start on boot, auto-restart on crash
- **NTFS & exFAT** USB drive support

---

## Supported Raspberry Pi OS

Tested on:
- Raspberry Pi OS **Bookworm** (64-bit & 32-bit)
- Raspberry Pi OS **Bullseye** (64-bit & 32-bit)

Works on Pi 3B+, 4B, 5, and Zero 2W.

---

## Installation

```bash
sudo apt-get install git
cd ~
git clone https://github.com/hajoemo/pi_video_looper_mpv
cd mpv_video_looper
sudo ./install.sh
```

Reboot and the looper starts automatically.

---

## How to update

```bash
# Back up your config first
sudo cp /boot/video_looper.ini /boot/video_looper.ini_backup

cd ~
sudo rm -rf mpv_video_looper
git clone https://github.com/hajoemo/pi_video_looper_mpv
cd mpv_video_looper
sudo ./install.sh
```

---

## Configuration

Edit `/boot/video_looper.ini` (accessible by inserting the SD card on any computer):

```bash
sudo nano /boot/video_looper.ini
```

Apply changes without rebooting:

```bash
./reload.sh
```

---

## Key settings at a glance

### `[video_looper]` section

| Key | Default | Description |
|---|---|---|
| `file_reader` | `usb_drive` | `usb_drive`, `directory`, or `usb_drive_copymode` |
| `osd` | `true` | Show/hide informational overlays |
| `countdown_time` | `5` | Seconds of countdown before playback |
| `wait_time` | `0` | Seconds to wait between files |
| `datetime_display` | `false` | Show clock between videos |
| `is_random` | `false` | Shuffle playback order |
| `is_random_unique` | `false` | No repeats until all files played |
| `resume_playlist` | `false` | Resume last file after reboot |
| `one_shot_playback` | `false` | Stop after each file |
| `play_on_startup` | `true` | Start playing immediately |
| `bgimage` | *(empty)* | Path to background image |
| `bgcolor` | `0, 0, 0` | R,G,B background color |
| `fgcolor` | `255, 255, 255` | R,G,B text color |
| `console_output` | `false` | Verbose logging |

### `[mpv]` section

| Key | Default | Description |
|---|---|---|
| `extensions` | `avi,mov,mkv,mp4,…` | Supported video extensions |
| `audio_device` | *(system default)* | ALSA/PulseAudio device (run `mpv --audio-device=help`) |
| `volume` | `100` | Playback volume 0–100 |
| `show_titles` | `false` | Overlay filename/M3U title during playback |
| `title_duration` | `4` | Seconds to show title |
| `extra_args` | *(empty)* | Extra mpv CLI flags |

---

## Keyboard commands

| Key | Action |
|---|---|
| `ESC` | Quit the looper |
| `k` | **K**ip – skip to next file |
| `b` | **B**ack – go to previous file |
| `s` | **S**top / start playback toggle |
| `p` | **P**ower off (shutdown RPi) |
| `Space` | Pause / resume |
| `o` | Next chapter |
| `i` | Previous chapter |

---

## Copy-mode

Enable with `file_reader = usb_drive_copymode`.

When a USB drive is inserted:
1. The looper looks for a file named `videopi` (configurable via `password`) on the drive root.
2. If found, it copies all video files to the local directory (`/home/pi/video` by default).
3. A **progress OSD** shows copy status.
4. Playback begins from the local copy.

Override copy mode per-drive by placing a file named `replace` or `add` in the drive root.

---

## GPIO control

Set `gpio_pin_map` in the `[control]` section. Pin numbers use **BOARD** numbering.

```ini
gpio_pin_map = "11" : 1, "13": 4, "16": "+2", "18": "-1", "15": "video.mp4", "19": "K_SPACE", "21": "K_p"
```

| Format | Example | Effect |
|---|---|---|
| Integer index | `"11" : 0` | Play first file |
| Quoted integer | `"11" : "3"` | Play 4th file (0-based) |
| Relative jump | `"16" : "+2"` | Skip forward 2 files |
| Filename | `"15" : "clip.mp4"` | Play named file |
| Keyboard command | `"19" : "K_SPACE"` | Pause / resume |
| Shutdown | `"21" : "K_p"` | Shut down Pi |

Bridge the mapped pin to any **Ground** pin to trigger it.

---

## `_repeat_Nx` filename tag

Append `_repeat_Nx` to any filename (before the extension) to play it N times before advancing:

```
intro_repeat_5x.mp4    → plays 5 times
bumper_repeat_2x.mp4   → plays 2 times
main.mp4               → plays once
```

---

## Playlist file (M3U)

Set `path = playlist.m3u` in the `[playlist]` section. Relative paths resolve from the `file_reader` root. See `assets/example.m3u` for the syntax.

---

## Image slideshow mode

Set `video_player = image_player` in `[video_looper]` and configure supported extensions and duration in the `[image_player]` section.

---

## Logs

```bash
sudo tail -f /var/log/supervisor/mpv_video_looper-stdout.log
sudo tail -f /var/log/supervisor/mpv_video_looper-stderr.log
```

Enable detailed logging: set `console_output = true` in `/boot/video_looper.ini`.

---

## Troubleshooting

**Nothing plays after inserting USB drive**
- Check if the password file exists on the drive root (only relevant in `usb_drive_copymode`).
- Enable `console_output = true` and check the logs.
- Run `lsblk` to confirm the drive is detected.

**No audio**
- Run `mpv --audio-device=help` and set `audio_device` in `[mpv]`.
- For HDMI audio: `audio_device = alsa/hdmi:CARD=vc4hdmi0`

**Black screen / display issues**
- Try adding `--vo=gpu --hwdec=auto` to `extra_args` in `[mpv]`.
- For older Pi models: `extra_args = --vo=drm`

**GPIO not working**
- Verify `RPi.GPIO` is installed: `python3 -c "import RPi.GPIO; print('OK')"`
- Make sure `keyboard_control = true` when using keyboard-command GPIO actions.

---

## License

GPL-2.0 – same as the original pi_video_looper.
