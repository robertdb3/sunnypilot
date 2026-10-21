import os
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import sys, glob, os, zstandard, capnp, cantools, numpy as np, pickle
capnp.remove_import_hook()
S = os.path.join(BASE,'schema')
log = capnp.load(f'{S}/log.capnp', imports=[S])
db = cantools.database.load_file(os.path.join(BASE,'outback2019.dbc'), strict=False)
WANT = {m.frame_id: m for m in db.messages if m.name in (
  'ES_Brake','ES_Distance','ES_Status','ES_DashStatus','CruiseControl','Throttle','Brake_Pedal','Brake_Pressure',
  'Engine','Transmission','G_Sensor','Wheel_Speeds','CVT_Ratio','Brake_2','Brake_Type','Stalk','ES_LDW')}

def read(path):
  data = zstandard.ZstdDecompressor().stream_reader(open(path, 'rb')).read()
  return log.Event.read_multiple_bytes(data, traversal_limit_in_words=2**63-1)

def extract(seg):
  can = {}   # (name, src) -> list of (t, dict)
  cs, sd, cc, lp = [], [], [], []
  for ev in read(seg):
    w = ev.which(); t = ev.logMonoTime * 1e-9
    if w == 'can':
      for m in ev.can:
        if m.address in WANT:
          msg = WANT[m.address]
          try: d = msg.decode(bytes(m.dat), decode_choices=False)
          except Exception: continue
          can.setdefault((msg.name, m.src), []).append((t, d))
    elif w == 'carState':
      c = ev.carState
      cs.append((t, c.vEgo, c.aEgo, c.cruiseState.enabled, c.gasPressed, c.brakePressed, c.cruiseState.speed, c.standstill))
    elif w == 'selfdriveState':
      s = ev.selfdriveState; sd.append((t, s.enabled, s.active))
    elif w == 'carControl':
      c = ev.carControl
      o = list(c.orientationNED); cc.append((t, c.enabled, c.latActive, c.longActive, o[1] if len(o) == 3 else np.nan))
    elif w == 'longitudinalPlan':
      p = ev.longitudinalPlan; lp.append((t, p.aTarget, p.hasLead))
  return dict(can={k: v for k, v in can.items()}, cs=np.array(cs), sd=np.array(sd), cc=np.array(cc), lp=np.array(lp))

for seg in sys.argv[1:]:
  name = seg.split('/')[-2]
  out = os.path.join(BASE,'out',f'{name}.pkl')
  if os.path.exists(out): continue
  pickle.dump(extract(seg), open(out, 'wb'))
  print('done', name, flush=True)
