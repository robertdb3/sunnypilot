#!/usr/bin/env python3
import unittest

from opendbc.car.structs import CarParams
from opendbc.car.subaru.values import SubaruSafetyFlags
import opendbc.safety.tests.common as common
class TestSubaruPreglobalSafety(common.CarSafetyTest, common.DriverTorqueSteeringSafetyTest):
  SAFETY_MODEL = CarParams.SafetyModel.subaruPreglobal

  DBC = "subaru_outback_2015_generated"
  TX_MSGS = [[0x161, 0], [0x164, 0]]
  RELAY_MALFUNCTION_ADDRS = {0: (0x164, 0x161)}
  FWD_BLACKLISTED_ADDRS = {2: [0x161, 0x164]}

  MAX_RATE_UP = 50
  MAX_RATE_DOWN = 70
  MAX_TORQUE_LOOKUP = [0], [2047]

  MAX_RT_DELTA = 940

  DRIVER_TORQUE_ALLOWANCE = 75
  DRIVER_TORQUE_FACTOR = 10

  def _set_prev_torque(self, t):
    self.safety.set_desired_torque_last(t)
    self.safety.set_rt_torque_last(t)

  def _torque_driver_msg(self, torque):
    values = {"Steer_Torque_Sensor": torque}
    return self.packer.make_can_msg_safety("Steering_Torque", 0, values)

  def _speed_msg(self, speed):
    # subaru safety doesn't use the scaled value, so undo the scaling
    values = {s: speed*0.0592 for s in ["FR", "FL", "RR", "RL"]}
    return self.packer.make_can_msg_safety("Wheel_Speeds", 0, values)

  def _user_brake_msg(self, brake):
    values = {"Brake_Pedal": brake}
    return self.packer.make_can_msg_safety("Brake_Pedal", 0, values)

  def _torque_cmd_msg(self, torque, steer_req=1):
    values = {"LKAS_Command": torque, "LKAS_Active": steer_req}
    return self.packer.make_can_msg_safety("ES_LKAS", 0, values)

  def _user_gas_msg(self, gas):
    values = {"Throttle_Pedal": gas}
    return self.packer.make_can_msg_safety("Throttle", 0, values)

  def _pcm_status_msg(self, enable):
    values = {"Cruise_Activated": enable}
    return self.packer.make_can_msg_safety("CruiseControl", 0, values)

  def test_stock_longitudinal_unchanged(self):
    # without the longitudinal flag, EyeSight's brake and RPM messages stay untouched,
    # and ES_Distance is not bounded by EyeSight's request
    if self.SAFETY_PARAM & SubaruSafetyFlags.LONG:
      raise unittest.SkipTest("longitudinal")
    for controls_allowed in (False, True):
      self.safety.set_controls_allowed(controls_allowed)
      self.assertFalse(self._tx(self.packer.make_can_msg_safety("ES_Brake", 0, {"Brake_Pressure": 0})))
      self.assertFalse(self._tx(self.packer.make_can_msg_safety("ES_Status", 0, {"Cruise_RPM": 0})))
      self.assertTrue(self._rx(self.packer.make_can_msg_safety("ES_Distance", 2, {"Cruise_Throttle": 1000})))
      for throttle in (0, 1000, 4095):
        self.assertTrue(self._tx(self.packer.make_can_msg_safety("ES_Distance", 0, {"Cruise_Throttle": throttle})))


class TestSubaruPreglobalReversedDriverTorqueSafety(TestSubaruPreglobalSafety):
  SAFETY_PARAM = SubaruSafetyFlags.PREGLOBAL_REVERSED_DRIVER_TORQUE

  DBC = "subaru_outback_2019_generated"


class TestSubaruPreglobalLongitudinalSafety(TestSubaruPreglobalReversedDriverTorqueSafety):
  """Blended longitudinal: openpilot may only add braking or remove throttle relative to EyeSight."""
  SAFETY_PARAM = SubaruSafetyFlags.PREGLOBAL_REVERSED_DRIVER_TORQUE | SubaruSafetyFlags.LONG

  TX_MSGS = [[0x160, 0], [0x161, 0], [0x162, 0], [0x164, 0]]
  RELAY_MALFUNCTION_ADDRS = {0: (0x160, 0x161, 0x162, 0x164)}
  FWD_BLACKLISTED_ADDRS = {2: [0x160, 0x161, 0x162, 0x164]}

  MAX_BRAKE = 600
  THROTTLE_ENGINE_BRAKE = 808

  def _cam(self, brake=None, throttle=None, rpm=None):
    if brake is not None:
      self.assertTrue(self._rx(self.packer.make_can_msg_safety("ES_Brake", 2, {"Brake_Pressure": brake})))
    if throttle is not None:
      self.assertTrue(self._rx(self.packer.make_can_msg_safety("ES_Distance", 2, {"Cruise_Throttle": throttle})))
    if rpm is not None:
      self.assertTrue(self._rx(self.packer.make_can_msg_safety("ES_Status", 2, {"Cruise_RPM": rpm})))

  def _brake_cmd(self, brake):
    return self._tx(self.packer.make_can_msg_safety("ES_Brake", 0, {"Brake_Pressure": brake}))

  def _throttle_cmd(self, throttle):
    return self._tx(self.packer.make_can_msg_safety("ES_Distance", 0, {"Cruise_Throttle": throttle}))

  def _rpm_cmd(self, rpm):
    return self._tx(self.packer.make_can_msg_safety("ES_Status", 0, {"Cruise_RPM": rpm}))

  def test_passthrough_when_not_allowed(self):
    self.safety.set_controls_allowed(False)
    for _ in range(3):
      self._cam(brake=150, throttle=2500, rpm=1200)
    self.assertTrue(self._brake_cmd(150))
    self.assertFalse(self._brake_cmd(149))
    self.assertFalse(self._brake_cmd(151))
    self.assertTrue(self._throttle_cmd(2500))
    self.assertFalse(self._throttle_cmd(2499))
    self.assertFalse(self._throttle_cmd(2501))
    self.assertTrue(self._rpm_cmd(1200))
    self.assertFalse(self._rpm_cmd(1199))
    self.assertFalse(self._rpm_cmd(1201))

  def test_camera_history_window(self):
    # openpilot rebuilds a camera message after the panda has seen it, so any of the last three are
    # accepted, and nothing older
    self.safety.set_controls_allowed(False)
    for brake, throttle, rpm in ((50, 1000, 900), (100, 2000, 1000), (150, 3000, 1100), (200, 2500, 1050)):
      self._cam(brake=brake, throttle=throttle, rpm=rpm)
    for brake in (100, 150, 200):
      self.assertTrue(self._brake_cmd(brake))
    self.assertFalse(self._brake_cmd(50))
    self.assertFalse(self._brake_cmd(99))
    self.assertFalse(self._brake_cmd(201))
    for throttle in (2000, 2500, 3000):
      self.assertTrue(self._throttle_cmd(throttle))
    self.assertFalse(self._throttle_cmd(1000))
    self.assertFalse(self._throttle_cmd(3001))
    for rpm in (1000, 1050, 1100):
      self.assertTrue(self._rpm_cmd(rpm))
    self.assertFalse(self._rpm_cmd(900))
    self.assertFalse(self._rpm_cmd(1101))

  def test_added_braking(self):
    for _ in range(3):
      self._cam(brake=0, throttle=2500, rpm=1200)
    for controls_allowed in (False, True):
      self.safety.set_controls_allowed(controls_allowed)
      self.assertTrue(self._brake_cmd(0))
      for brake in (1, 300, self.MAX_BRAKE):
        self.assertEqual(controls_allowed, self._brake_cmd(brake))
      self.assertFalse(self._brake_cmd(self.MAX_BRAKE + 1))

  def test_eyesight_braking_is_a_floor(self):
    # openpilot can never brake less than EyeSight, and EyeSight braking beyond the openpilot
    # limit (AEB) passes through
    for controls_allowed in (False, True):
      self.safety.set_controls_allowed(controls_allowed)
      for _ in range(3):
        self._cam(brake=900)
      self.assertTrue(self._brake_cmd(900))
      self.assertFalse(self._brake_cmd(899))
      self.assertFalse(self._brake_cmd(self.MAX_BRAKE))
      self.assertFalse(self._brake_cmd(901))

  def test_no_added_braking_on_gas_press(self):
    self.safety.set_controls_allowed(True)
    for _ in range(3):
      self._cam(brake=0)
    self.assertTrue(self._brake_cmd(300))
    self._rx(self._user_gas_msg(1))
    self.assertFalse(self._brake_cmd(300))
    self.assertTrue(self._brake_cmd(0))
    self._rx(self._user_gas_msg(0))
    self._rx(self._user_gas_msg(0))
    self.assertTrue(self._brake_cmd(300))

  def test_no_added_braking_on_brake_press(self):
    self.safety.set_controls_allowed(True)
    for _ in range(3):
      self._cam(brake=0)
    self.assertTrue(self._brake_cmd(300))
    self._rx(self._user_brake_msg(1))
    self.assertFalse(self._brake_cmd(300))
    self.assertTrue(self._brake_cmd(0))

  def test_throttle_reduction(self):
    for _ in range(3):
      self._cam(throttle=2500)
    for controls_allowed in (False, True):
      self.safety.set_controls_allowed(controls_allowed)
      self.assertTrue(self._throttle_cmd(2500))
      self.assertFalse(self._throttle_cmd(2501))
      self.assertFalse(self._throttle_cmd(4095))
      for throttle in (self.THROTTLE_ENGINE_BRAKE, 1500, 2499):
        self.assertEqual(controls_allowed, self._throttle_cmd(throttle))
      self.assertFalse(self._throttle_cmd(self.THROTTLE_ENGINE_BRAKE - 1))
      self.assertFalse(self._throttle_cmd(0))

  def test_throttle_ignores_neighbouring_signals(self):
    # Cruise_Throttle shares its bytes with Signal1, which EyeSight sets while driving
    self.safety.set_controls_allowed(False)
    for _ in range(3):
      self.assertTrue(self._rx(self.packer.make_can_msg_safety("ES_Distance", 2, {"Cruise_Throttle": 2500, "Signal1": 2})))
    self.assertTrue(self._tx(self.packer.make_can_msg_safety("ES_Distance", 0, {"Cruise_Throttle": 2500, "Signal1": 2})))
    self.assertTrue(self._tx(self.packer.make_can_msg_safety("ES_Distance", 0, {"Cruise_Throttle": 2500, "Signal1": 15})))
    self.assertFalse(self._tx(self.packer.make_can_msg_safety("ES_Distance", 0, {"Cruise_Throttle": 2501, "Signal1": 2})))

  def test_throttle_below_engine_brake_passes_through(self):
    # EyeSight occasionally requests less than its engine braking value; that stays allowed
    self.safety.set_controls_allowed(True)
    for _ in range(3):
      self._cam(throttle=500)
    self.assertTrue(self._throttle_cmd(500))
    self.assertFalse(self._throttle_cmd(499))
    self.assertFalse(self._throttle_cmd(501))

  def test_no_eyesight_request_seen(self):
    # before the camera has sent anything, openpilot may only send zeros, plus added braking when engaged
    self.safety.set_controls_allowed(False)
    self.assertTrue(self._brake_cmd(0))
    self.assertFalse(self._brake_cmd(1))
    self.assertTrue(self._throttle_cmd(0))
    self.assertFalse(self._throttle_cmd(1))
    self.assertTrue(self._rpm_cmd(0))
    self.assertFalse(self._rpm_cmd(1))
    self.safety.set_controls_allowed(True)
    self.assertTrue(self._brake_cmd(self.MAX_BRAKE))
    self.assertFalse(self._throttle_cmd(1))

  def test_history_resets_on_init(self):
    self.safety.set_controls_allowed(False)
    for _ in range(3):
      self._cam(brake=150, throttle=2500, rpm=1200)
    self._reset_safety_hooks()
    self.assertFalse(self._brake_cmd(150))
    self.assertFalse(self._throttle_cmd(2500))
    self.assertFalse(self._rpm_cmd(1200))

  def test_stop_and_go_with_longitudinal(self):
    self.safety.set_current_safety_param_sp(1)
    self._reset_safety_hooks()
    self.safety.set_controls_allowed(True)
    for _ in range(3):
      self._cam(brake=0, throttle=2500, rpm=1200)
    self.assertTrue(self._brake_cmd(300))
    self.assertTrue(self._throttle_cmd(self.THROTTLE_ENGINE_BRAKE))
    self.assertTrue(self._rpm_cmd(1200))
    self.assertTrue(self._tx(self.packer.make_can_msg_safety("Throttle", 2, {"Throttle_Pedal": 5})))
    self.assertTrue(self._tx(self.packer.make_can_msg_safety("Brake_Pedal", 2, {"Speed": 1})))
    self.safety.set_current_safety_param_sp(0)


if __name__ == "__main__":
  unittest.main()
