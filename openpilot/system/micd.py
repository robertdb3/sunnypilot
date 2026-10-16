#!/usr/bin/env python3
import ctypes
import ctypes.util
import os
import re
import subprocess
import numpy as np
from functools import cache
import threading

from openpilot.cereal import messaging
from openpilot.common.realtime import Ratekeeper
from openpilot.common.utils import retry
from openpilot.common.swaglog import cloudlog

RATE = 10
FFT_SAMPLES = 1600 # 100ms
REFERENCE_SPL = 2e-5  # newtons/m^2
SAMPLE_RATE = 16000
SAMPLE_BUFFER = 800  # 50ms

# On the first drive after boot the audio device can stay unopenable for ~30s after onroad
# starts, which outlasted 10 x 3s and left micd and soundd dead for the whole drive.
# Shared with soundd.
STREAM_OPEN_ATTEMPTS = 40
STREAM_OPEN_DELAY = 3  # seconds


# PortAudio only says "Error querying device -1" while the device is unopenable, which doesn't say
# which layer is refusing. Snapshot each layer on every failed open. Shared with soundd.
AUDIO_DMESG_RE = re.compile(r"snd|asoc|pcm|q6|apr|adsp|audio|afe|wcd|tavil", re.IGNORECASE)


def _read(path):
  with open(path) as f:
    return f.read().strip()


def _dev_snd_holders():
  # Only processes we're allowed to inspect, i.e. our own user's.
  holders = {}
  for pid in filter(str.isdigit, os.listdir("/proc")):
    try:
      fds = os.listdir(f"/proc/{pid}/fd")
    except OSError:
      continue
    for fd in fds:
      try:
        target = os.readlink(f"/proc/{pid}/fd/{fd}")
      except OSError:
        continue
      if target.startswith("/dev/snd/pcm"):
        holders.setdefault(f"{pid}:{_read(f'/proc/{pid}/comm')}", []).append(target)
  return holders


def _dmesg_audio(limit=20):
  # Every PCM open repeats the same few lines, so keep the latest of each with a count.
  out = subprocess.run(["dmesg"], capture_output=True, text=True, timeout=2).stdout
  latest, counts = {}, {}
  for line in out.splitlines():
    if not AUDIO_DMESG_RE.search(line) or "wlan:" in line:
      continue
    msg = line.split("] ", 1)[-1]
    latest.pop(msg, None)
    latest[msg] = line
    counts[msg] = counts.get(msg, 0) + 1
  return [f"{line} (x{counts[msg]})" if counts[msg] > 1 else line for msg, line in list(latest.items())[-limit:]]


def _open_dev_node(path):
  st = os.stat(path)
  info = f"{st.st_uid}:{st.st_gid} {oct(st.st_mode & 0o777)}"
  try:
    os.close(os.open(path, os.O_RDWR | os.O_NONBLOCK))
    return f"{info} open ok"
  except OSError as e:
    return f"{info} open {e.strerror}"


def _alsa_pcm_open(name, capture):
  asound = ctypes.CDLL(ctypes.util.find_library("asound") or "libasound.so.2")
  asound.snd_strerror.restype = ctypes.c_char_p
  handle = ctypes.c_void_p()
  # SND_PCM_STREAM_PLAYBACK=0/CAPTURE=1, SND_PCM_NONBLOCK=1
  rc = asound.snd_pcm_open(ctypes.byref(handle), name.encode(), int(capture), 1)
  if rc == 0:
    asound.snd_pcm_close(handle)
    return "ok"
  return f"{rc} {asound.snd_strerror(rc).decode()}"


def audio_open_diagnostics(sd, capture):
  diag = {}

  def probe(key, fn):
    try:
      diag[key] = fn()
    except Exception as e:
      diag[key] = f"{type(e).__name__}: {e}"

  probe("uptime", lambda: float(_read("/proc/uptime").split()[0]))
  # Before our own probes: every PCM open adds a q6asm line to dmesg.
  probe("dmesg_audio", _dmesg_audio)
  probe("groups", os.getgroups)
  probe("cards", lambda: _read("/proc/asound/cards"))
  probe("card0_state", lambda: _read("/proc/asound/card0/state"))
  # Only our own direction: opening the other one could collide with micd/soundd's own retry.
  for node in ("controlC0", "pcmC0D0c" if capture else "pcmC0D0p"):
    probe(f"node_{node}", lambda node=node: _open_dev_node(f"/dev/snd/{node}"))
  for name in ("default", "hw:0,0"):
    probe(f"alsa_{name}", lambda name=name: _alsa_pcm_open(name, capture))
  probe("portaudio_devices", lambda: len(sd.query_devices()))
  probe("portaudio_default", lambda: list(sd.default.device))
  probe("dev_snd_holders", _dev_snd_holders)
  return diag


def patch_sounddevice(sd):
  # TODO: remove once sounddevice uses np.reshape internally.
  def sounddevice_array(buffer, channels, dtype):
    return np.frombuffer(buffer, dtype=dtype).reshape(-1, channels)

  sd._array = sounddevice_array


@cache
def get_a_weighting_filter():
  # Calculate the A-weighting filter
  # https://en.wikipedia.org/wiki/A-weighting
  freqs = np.fft.fftfreq(FFT_SAMPLES, d=1 / SAMPLE_RATE)
  A = 12194 ** 2 * freqs ** 4 / ((freqs ** 2 + 20.6 ** 2) * (freqs ** 2 + 12194 ** 2) * np.sqrt((freqs ** 2 + 107.7 ** 2) * (freqs ** 2 + 737.9 ** 2)))
  return A / np.max(A)


def calculate_spl(measurements):
  # https://www.engineeringtoolbox.com/sound-pressure-d_711.html
  sound_pressure = np.sqrt(np.mean(measurements ** 2))  # RMS of amplitudes
  if sound_pressure > 0:
    sound_pressure_level = 20 * np.log10(sound_pressure / REFERENCE_SPL)  # dB
  else:
    sound_pressure_level = 0
  return sound_pressure, sound_pressure_level


def apply_a_weighting(measurements: np.ndarray) -> np.ndarray:
  # Generate a Hanning window of the same length as the audio measurements
  measurements_windowed = measurements * np.hanning(len(measurements))

  # Apply the A-weighting filter to the signal
  return np.abs(np.fft.ifft(np.fft.fft(measurements_windowed) * get_a_weighting_filter()))


class Mic:
  def __init__(self):
    self.rk = Ratekeeper(RATE)
    self.pm = messaging.PubMaster(['soundPressure', 'rawAudioData'])

    self.measurements = np.empty(0)

    self.sound_pressure = 0
    self.sound_pressure_weighted = 0
    self.sound_pressure_level_weighted = 0

    self.lock = threading.Lock()

  def update(self):
    with self.lock:
      sound_pressure = self.sound_pressure
      sound_pressure_weighted = self.sound_pressure_weighted
      sound_pressure_level_weighted = self.sound_pressure_level_weighted

    msg = messaging.new_message('soundPressure', valid=True)
    msg.soundPressure.soundPressure = float(sound_pressure)
    msg.soundPressure.soundPressureWeighted = float(sound_pressure_weighted)
    msg.soundPressure.soundPressureWeightedDb = float(sound_pressure_level_weighted)

    self.pm.send('soundPressure', msg)
    self.rk.keep_time()

  def callback(self, indata, frames, time, status):
    """
    Using amplitude measurements, calculate an uncalibrated sound pressure and sound pressure level.
    Then apply A-weighting to the raw amplitudes and run the same calculations again.

    Logged A-weighted equivalents are rough approximations of the human-perceived loudness.
    """
    msg = messaging.new_message('rawAudioData', valid=True)
    audio_data_int_16 = (indata[:, 0] * 32767).astype(np.int16)
    msg.rawAudioData.data = audio_data_int_16.tobytes()
    msg.rawAudioData.sampleRate = SAMPLE_RATE
    self.pm.send('rawAudioData', msg)

    with self.lock:
      self.measurements = np.concatenate((self.measurements, indata[:, 0]))

      while self.measurements.size >= FFT_SAMPLES:
        measurements = self.measurements[:FFT_SAMPLES]

        self.sound_pressure, _ = calculate_spl(measurements)
        measurements_weighted = apply_a_weighting(measurements)
        self.sound_pressure_weighted, self.sound_pressure_level_weighted = calculate_spl(measurements_weighted)

        self.measurements = self.measurements[FFT_SAMPLES:]

  @retry(attempts=STREAM_OPEN_ATTEMPTS, delay=STREAM_OPEN_DELAY)
  def get_stream(self, sd):
    try:
      # reload sounddevice to reinitialize portaudio
      sd._terminate()
      sd._initialize()
      return sd.InputStream(channels=1, samplerate=SAMPLE_RATE, callback=self.callback, blocksize=SAMPLE_BUFFER)
    except Exception:
      # retry() only prints, so log the actual cause
      cloudlog.exception("micd failed to open input stream")
      cloudlog.event("micd audio open diagnostics", error=True, **audio_open_diagnostics(sd, capture=True))
      raise

  def micd_thread(self):
    # sounddevice must be imported after forking processes
    import sounddevice as sd
    patch_sounddevice(sd)

    with self.get_stream(sd) as stream:
      cloudlog.info(f"micd stream started: {stream.samplerate=} {stream.channels=} {stream.dtype=} {stream.device=}, {stream.blocksize=}")
      while True:
        self.update()


def main():
  mic = Mic()
  mic.micd_thread()


if __name__ == "__main__":
  main()
