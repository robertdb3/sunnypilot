import os
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import glob,os,zstandard,capnp,numpy as np
capnp.remove_import_hook(); S=os.path.join(BASE,'schema')
log=capnp.load(f'{S}/log.capnp',imports=[S])
R={0x22a:[],0x325:[]}; lead=[]; cs=[]
for f in sorted(glob.glob(os.path.join(BASE,'logs/000000c0*/rlog.zst')))[:12]:
  data=zstandard.ZstdDecompressor().stream_reader(open(f,'rb')).read()
  for ev in log.Event.read_multiple_bytes(data,traversal_limit_in_words=2**63-1):
    w=ev.which(); t=ev.logMonoTime*1e-9
    if w=='can':
      for m in ev.can:
        if m.src==2 and m.address in R: R[m.address].append((t,int.from_bytes(bytes(m.dat),'little')))
    elif w=='modelV2':
      l=ev.modelV2.leadsV3
      lead.append((t,l[0].prob if len(l) else 0, l[0].x[0] if len(l) else np.nan))
    elif w=='carState': cs.append((t,ev.carState.vEgo,ev.carState.aEgo))
lead=np.array(lead); cs=np.array(cs)
for ad,v in R.items():
  t=np.array([x[0] for x in v]); raw=[x[1] for x in v]
  print(f"0x{ad:03x}: {len(v)} msgs, {len(v)/(t[-1]-t[0]):.0f} Hz")
  # try every byte-aligned 8/12/16-bit LE field; report best correlation with lead distance, speed
  ld=np.interp(t,lead[:,0],lead[:,2]); lp=np.interp(t,lead[:,0],lead[:,1]); vv=np.interp(t,cs[:,0],cs[:,1])
  m=lp>0.5
  for start in range(0,64,4):
    for width in (8,12,16):
      if start+width>64: continue
      f=np.array([(x>>start)&((1<<width)-1) for x in raw],float)
      if f.std()==0: continue
      c1=np.corrcoef(f[m],ld[m])[0,1] if m.sum()>100 else np.nan; c2=np.corrcoef(f,vv)[0,1]
      if abs(c1)>0.7 or abs(c2)>0.7: print(f"   bits {start}+{width}: corr lead_dist {c1:+.2f}, corr speed {c2:+.2f}, range {f.min():.0f}-{f.max():.0f}")
