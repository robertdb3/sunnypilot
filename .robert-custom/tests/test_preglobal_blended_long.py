"""Tests for preglobal blended longitudinal (patch 0020).

    python3 tests/test_preglobal_blended_long.py

Unlike most suites here this drives the real opendbc Subaru CarInterface from the candidate tree.
opendbc's car code is pure Python, so it runs on plain python3 with numpy and pycapnp. CAN traffic is
synthesised with the candidate's own packer.

What has to stay true:
  - toggle off: nothing about the car's CAN output changes
  - toggle on, not engaged: every EyeSight message is rebuilt byte for byte, one for one
  - engaged: braking only ever goes up, throttle only ever goes down, within the panda's limits
  - the Python limits and the panda safety limits agree
"""
import os
import re
import sys
import unittest

REPO = os.path.join(os.path.dirname(__file__), "..", "sunnypilot")
sys.path.insert(0, os.path.join(REPO, "opendbc_repo"))

from opendbc.can import CANPacker  # noqa: E402
from opendbc.car import gen_empty_fingerprint, structs  # noqa: E402
from opendbc.car.subaru import preglobal_long  # noqa: E402
from opendbc.car.subaru.interface import CarInterface  # noqa: E402
from opendbc.car.subaru.values import CAR  # noqa: E402

DBC = "subaru_outback_2019_generated"
SAFETY = os.path.join(REPO, "opendbc_repo", "opendbc", "safety", "modes", "subaru_preglobal.h")
ES_BRAKE, ES_DISTANCE, ES_STATUS = 0x160, 0x161, 0x162
CHECKSUM_BYTE = {ES_BRAKE: 7, ES_DISTANCE: 7, ES_STATUS: 4}


def u16(dat, offset):
  return dat[offset] | (dat[offset + 1] << 8)


def checksum_ok(addr, dat):
  cb = CHECKSUM_BYTE[addr]
  return (sum(dat[:cb]) + sum(dat[cb + 1:])) % 256 == dat[cb]


class Car:
  """The real CarInterface, fed synthetic CAN one 100 Hz control cycle at a time, like card does."""

  def __init__(self, alpha_long):
    fp = gen_empty_fingerprint()
    self.CP = CarInterface.get_params(CAR.SUBARU_OUTBACK_PREGLOBAL_2018, fp, [], alpha_long=alpha_long, is_release=False, docs=False)
    CP_SP = CarInterface.get_params_sp(self.CP, CAR.SUBARU_OUTBACK_PREGLOBAL_2018, fp, [], alpha_long=alpha_long,
                                       is_release_sp=False, docs=False)
    self.CI = CarInterface(self.CP, CP_SP)
    self.packer = CANPacker(DBC)
    self.frame = 0
    self.counter = 0
    self.t = 0
    self.camera = {ES_BRAKE: [], ES_DISTANCE: [], ES_STATUS: []}

  def _pt(self, cruise_engaged, speed, gas, brake_pedal):
    p = self.packer
    return [
      p.make_can_msg("CruiseControl", 0, {"Cruise_On": 1, "Cruise_Activated": int(cruise_engaged)}),
      p.make_can_msg("Wheel_Speeds", 0, {s: speed * 3.6 for s in ("FL", "FR", "RL", "RR")}),
      p.make_can_msg("Throttle", 0, {"Throttle_Pedal": gas}),
      p.make_can_msg("Brake_Pedal", 0, {"Brake_Pedal": brake_pedal}),
      p.make_can_msg("Transmission", 0, {"Gear": 4}),
      p.make_can_msg("Dashlights", 0, {}),
      p.make_can_msg("BodyInfo", 0, {}),
      p.make_can_msg("Steering_Torque", 0, {}),
      p.make_can_msg("Dash_State2", 0, {}),
      p.make_can_msg("ES_DashStatus", 2, {"Cruise_On": 1}),
    ]

  def _camera(self, cruise_engaged, es_brake, es_throttle, es_rpm, es_lights, es_brake_active, es_button):
    counter = self.counter % 8
    self.counter += 1
    p = self.packer
    brake_active = int(es_brake > 0) if es_brake_active is None else es_brake_active
    cam = {
      ES_BRAKE: p.make_can_msg("ES_Brake", 2, {"Brake_Pressure": es_brake, "Cruise_Brake_Active": brake_active,
                                               "Cruise_Brake_Lights": es_lights, "Cruise_Activated": int(cruise_engaged),
                                               "SET_1": 1, "COUNTER": counter}),
      ES_DISTANCE: p.make_can_msg("ES_Distance", 2, {"Cruise_Throttle": es_throttle, "Signal1": 2, "Car_Follow": 1,
                                                    "Cruise_Brake_Active": brake_active, "Cruise_Button": es_button,
                                                    "COUNTER": counter}),
      ES_STATUS: p.make_can_msg("ES_Status", 2, {"Brake": brake_active, "Cruise_Activated": int(cruise_engaged),
                                                 "Cruise_RPM": es_rpm, "COUNTER": counter}),
    }
    frames = []
    for addr, (a, dat, bus) in cam.items():
      # give EyeSight's messages valid checksums, as the real camera does
      dat = bytearray(dat)
      cb = CHECKSUM_BYTE[addr]
      dat[cb] = (sum(dat[:cb]) + sum(dat[cb + 1:])) % 256
      frames.append((a, bytes(dat), bus))
      self.camera[addr].append(bytes(dat))
    return frames

  def step(self, cruise_engaged=True, speed=20., gas=0, brake_pedal=0, long_active=False, accel=0.,
           es_brake=0, es_throttle=2500, es_rpm=1500, es_lights=0, es_brake_active=None, es_buttons=(0,), pitch=None):
    """One control cycle. EyeSight sends its three longitudinal messages every fifth cycle (20 Hz).
    Passing several es_buttons delivers that many camera messages in this one cycle, as a delayed
    control loop would see them."""
    frames = self._pt(cruise_engaged, speed, gas, brake_pedal)
    if self.frame % 5 == 0:
      for es_button in es_buttons:
        frames += self._camera(cruise_engaged, es_brake, es_throttle, es_rpm, es_lights, es_brake_active, es_button)

    self.t += 10_000_000
    CS, _ = self.CI.update([(self.t, frames)])
    CC = structs.CarControl()
    CC.enabled = cruise_engaged
    CC.longActive = long_active
    CC.actuators.accel = accel
    if pitch is not None:
      CC.orientationNED = [0., pitch, 0.]
    _, sends = self.CI.apply(CC.as_reader(), structs.CarControlSP(), self.t)
    self.frame += 1
    return CS, [(addr, bytes(dat), bus) for addr, dat, bus in sends]

  def run(self, cycles, **kw):
    sent = []
    for _ in range(cycles):
      sent += self.step(**kw)[1]
    return sent


def by_addr(sends, addr):
  return [dat for a, dat, _ in sends if a == addr]


class TestLimitsAgreeWithPanda(unittest.TestCase):
  def test_constants_match_safety(self):
    with open(SAFETY) as f:
      src = f.read()
    self.assertEqual(int(re.search(r"#define SUBARU_PG_MAX_BRAKE\s+(\d+)U", src).group(1)), preglobal_long.BRAKE_MAX)
    self.assertEqual(int(re.search(r"#define SUBARU_PG_THROTTLE_ENGINE_BRAKE\s+(\d+)U", src).group(1)),
                     preglobal_long.THROTTLE_ENGINE_BRAKE)


class TestAccelToCommands(unittest.TestCase):
  def test_brake_rises_monotonically_and_is_capped(self):
    for v in (0., 10., 30.):
      last = -1
      for a in [x / 10 for x in range(0, -60, -1)]:
        brake, throttle = preglobal_long.accel_to_commands(a, v)
        self.assertGreaterEqual(brake, last)
        self.assertLessEqual(brake, preglobal_long.BRAKE_MAX)
        if brake > 0:
          self.assertEqual(throttle, preglobal_long.THROTTLE_ENGINE_BRAKE)
        last = brake

  def test_throttle_ceiling_rises_monotonically(self):
    for v in (5., 20., 35.):
      last = 0
      for a in [x / 20 for x in range(-10, 40)]:
        brake, throttle = preglobal_long.accel_to_commands(a, v)
        if brake == 0:
          self.assertGreaterEqual(throttle, last)
          last = throttle
      self.assertEqual(last, preglobal_long.THROTTLE_NO_LIMIT, "a strong request must not cap EyeSight at all")

  def test_throttle_ceiling_between_last_table_points(self):
    # just below the top of the table the ceiling is still a real limit
    for v in (5., 15., 25., 35.):
      row = preglobal_long.THROTTLE_ACCEL[preglobal_long.SPEED_BP.index(v)]
      _, throttle = preglobal_long.accel_to_commands((row[-2] + row[-1]) / 2, v)
      self.assertTrue(preglobal_long.THROTTLE_BP[-2] < throttle < preglobal_long.THROTTLE_BP[-1], throttle)

  def test_coasting_needs_no_brake(self):
    # asking for exactly what the car does at EyeSight's engine-braking throttle is not braking
    for v in (5., 15., 25., 35.):
      coast = preglobal_long.THROTTLE_ACCEL[preglobal_long.SPEED_BP.index(v)][0]
      self.assertEqual(preglobal_long.accel_to_commands(coast, v), (0, preglobal_long.THROTTLE_ENGINE_BRAKE))

  def test_standstill_hold_matches_eyesight(self):
    # openpilot's stopping state asks for stopAccel (-2.0); EyeSight itself holds the car at ~294
    brake, _ = preglobal_long.accel_to_commands(-2.0, 0.)
    self.assertTrue(250 <= brake <= 350, brake)


class TestToggleOff(unittest.TestCase):
  def test_stock_cadence_and_messages(self):
    car = Car(alpha_long=False)
    self.assertFalse(car.CP.openpilotLongitudinalControl)
    sent = car.run(100, long_active=True, accel=-3.0)
    self.assertEqual(by_addr(sent, ES_BRAKE), [])
    self.assertEqual(by_addr(sent, ES_STATUS), [])
    # ES_Distance keeps its every-fifth-frame cadence, with EyeSight's throttle untouched
    distance = by_addr(sent, ES_DISTANCE)
    self.assertEqual(len(distance), 20)
    self.assertTrue(all(u16(d, 0) & 0xFFF in (0, 2500) for d in distance))


class TestPassThrough(unittest.TestCase):
  def test_not_engaged_is_byte_identical(self):
    car = Car(alpha_long=True)
    self.assertTrue(car.CP.openpilotLongitudinalControl)
    sent = car.run(200, cruise_engaged=False, long_active=False, accel=-3.0, es_brake=120, es_throttle=1900, es_rpm=1300)
    for addr in (ES_BRAKE, ES_DISTANCE, ES_STATUS):
      rebuilt = by_addr(sent, addr)
      # the parser registers each message on its first appearance, so that one is not rebuilt
      self.assertEqual(rebuilt, car.camera[addr][1:], hex(addr))

  def test_not_engaged_passes_inconsistent_flags_through(self):
    # pass-through copies EyeSight verbatim, even flags that disagree with its brake pressure
    car = Car(alpha_long=True)
    sent = car.run(100, cruise_engaged=True, long_active=False, es_brake=0, es_brake_active=1)
    self.assertEqual(by_addr(sent, ES_BRAKE), car.camera[ES_BRAKE][1:])
    self.assertEqual(by_addr(sent, ES_STATUS), car.camera[ES_STATUS][1:])

  def test_engaged_without_request_is_byte_identical(self):
    # longActive with a request EyeSight already satisfies changes nothing
    car = Car(alpha_long=True)
    sent = car.run(200, long_active=True, accel=2.0, es_brake=0, es_throttle=2500)
    for addr in (ES_BRAKE, ES_DISTANCE, ES_STATUS):
      self.assertEqual(by_addr(sent, addr), car.camera[addr][1:], hex(addr))


class TestBlend(unittest.TestCase):
  def test_adds_braking_and_cuts_throttle(self):
    car = Car(alpha_long=True)
    sent = car.run(200, long_active=True, accel=-2.0, es_brake=0, es_throttle=2600, es_rpm=1400)
    brakes, distances, statuses = (by_addr(sent, a) for a in (ES_BRAKE, ES_DISTANCE, ES_STATUS))
    self.assertEqual(len(brakes), len(car.camera[ES_BRAKE]) - 1, "one rebuild per camera message")
    for dat in brakes:
      self.assertTrue(0 < u16(dat, 0) <= preglobal_long.BRAKE_MAX)
      self.assertEqual((dat[2] >> 6) & 1, 1, "Cruise_Brake_Active")
      self.assertEqual((dat[2] >> 4) & 1, 1, "brake lights for added braking")
    for dat in distances:
      self.assertEqual(u16(dat, 0) & 0xFFF, preglobal_long.THROTTLE_ENGINE_BRAKE)
      self.assertEqual((dat[2] >> 4) & 1, 1, "ES_Distance Cruise_Brake_Active")
      self.assertEqual((dat[1] >> 4) & 0xF, 2, "neighbouring signals pass through")
    for dat in statuses:
      self.assertEqual(u16(dat, 2), 1400, "Cruise_RPM passes through")
      self.assertEqual(dat[1] & 1, 1, "ES_Status Brake")

  def test_eyesight_braking_is_a_floor(self):
    # AEB-like EyeSight braking beyond openpilot's request and limit passes through untouched
    car = Car(alpha_long=True)
    sent = car.run(100, long_active=True, accel=-1.0, es_brake=900, es_throttle=808, es_lights=1)
    for dat in by_addr(sent, ES_BRAKE):
      self.assertEqual(u16(dat, 0), 900)
    self.assertEqual(by_addr(sent, ES_BRAKE), car.camera[ES_BRAKE][1:])

  def test_never_more_throttle_than_eyesight(self):
    car = Car(alpha_long=True)
    for accel in (-0.3, 0.0, 0.5, 2.0):
      for dat in by_addr(car.run(20, long_active=True, accel=accel, es_throttle=1500), ES_DISTANCE):
        self.assertLessEqual(u16(dat, 0) & 0xFFF, 1500)

  def test_counters_and_checksums(self):
    car = Car(alpha_long=True)
    sent = car.run(200, long_active=True, accel=-1.5)
    for addr in (ES_BRAKE, ES_DISTANCE, ES_STATUS):
      rebuilt = by_addr(sent, addr)
      self.assertTrue(rebuilt)
      for ours, camera in zip(rebuilt, car.camera[addr][1:], strict=True):
        self.assertTrue(checksum_ok(addr, ours))
        if addr == ES_DISTANCE:
          self.assertEqual((ours[5] >> 4) & 7, (camera[5] >> 4) & 7)
        else:
          self.assertEqual(ours[6] & 7, camera[6] & 7)

  def test_every_camera_message_in_a_cycle_is_rebuilt(self):
    # a delayed control loop sees two camera messages at once: both are rebuilt, in order, each with
    # the driver's own button from that message
    car = Car(alpha_long=True)
    car.run(10, long_active=True, accel=-1.0)
    sent = car.step(long_active=True, accel=-1.0, es_buttons=(0, 3))[1]
    self.assertEqual(len(by_addr(sent, ES_BRAKE)), 2)
    distance = by_addr(sent, ES_DISTANCE)
    self.assertEqual([(d[6] & 7) for d in distance], [0, 3])
    self.assertEqual([(d[5] >> 4) & 7 for d in distance], [(c[5] >> 4) & 7 for c in car.camera[ES_DISTANCE][-2:]])

  def test_disengage_restores_eyesight_immediately(self):
    car = Car(alpha_long=True)
    car.run(100, long_active=True, accel=-2.0, es_brake=0)
    sent = car.run(100, long_active=False, accel=-2.0, es_brake=0)
    self.assertTrue(all(u16(dat, 0) == 0 for dat in by_addr(sent, ES_BRAKE)))
    self.assertTrue(all((dat[2] >> 4) & 1 == 0 for dat in by_addr(sent, ES_BRAKE)), "brake lights released")

  def test_downhill_needs_more_braking(self):
    flat = by_addr(Car(alpha_long=True).run(200, long_active=True, accel=-1.0, pitch=0.0), ES_BRAKE)
    downhill = by_addr(Car(alpha_long=True).run(200, long_active=True, accel=-1.0, pitch=-0.05), ES_BRAKE)
    self.assertGreater(u16(downhill[-1], 0), u16(flat[-1], 0))


class TestBrakeLights(unittest.TestCase):
  def test_reengaging_does_not_reuse_old_lights(self):
    car = Car(alpha_long=True)
    car.run(50, long_active=True, accel=-2.0, es_brake=0)                  # openpilot braking: lights on
    car.run(10, long_active=False, es_brake=0)                              # disengage
    sent = car.run(50, long_active=True, accel=0.5, es_brake=50, es_lights=0)  # EyeSight braking lightly
    self.assertTrue(all((dat[2] >> 4) & 1 == 0 for dat in by_addr(sent, ES_BRAKE)))

  def test_hysteresis(self):
    blend = preglobal_long.PreglobalBlend()
    msg = {"Brake_Pressure": 0, "Cruise_Brake_Lights": 0, "Cruise_Fault": 0, "Cruise_Brake_Active": 0,
           "Cruise_Activated": 1, "SET_1": 1, "COUNTER": 0}
    cc = structs.CarControl()
    cc.longActive = True

    def lights(request_brake):
      blend.active, blend.brake = True, request_brake
      return blend.es_brake(msg)["Cruise_Brake_Lights"]

    self.assertEqual(lights(60), 0)    # light braking: no lights yet
    self.assertEqual(lights(80), 1)    # firm braking: on
    self.assertEqual(lights(30), 1)    # easing off: stays on
    self.assertEqual(lights(10), 0)    # nearly released: off
    blend.update(cc.as_reader(), 10.)  # sanity: update runs with a reader


if __name__ == "__main__":
  unittest.main()
