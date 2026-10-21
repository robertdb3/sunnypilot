import os
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import sys,glob,os,zstandard,capnp,numpy as np
from concurrent.futures import ProcessPoolExecutor
capnp.remove_import_hook()
S=os.path.join(BASE,'schema')
def scan(path):
  log=capnp.load(f'{S}/log.capnp',imports=[S])
  data=zstandard.ZstdDecompressor().stream_reader(open(path,'rb')).read()
  en=mov=0; vmax=0; dec=0
  for ev in log.Event.read_multiple_bytes(data,traversal_limit_in_words=2**63-1):
    if ev.which()=='carState':
      c=ev.carState
      if c.vEgo>1: mov+=1
      if c.cruiseState.enabled and c.vEgo>1: en+=1; vmax=max(vmax,c.vEgo)
      if c.cruiseState.enabled and c.aEgo<-1.0 and not c.brakePressed: dec+=1
  return path.split('/')[-2], mov, en, vmax*2.237, dec
if __name__=='__main__':
  fs=sorted(glob.glob(os.path.join(BASE,'qlogs/*/qlog.zst')))
  with ProcessPoolExecutor(8) as ex: res=list(ex.map(scan,fs))
  import collections; R=collections.defaultdict(lambda:[0,0,0,0])
  rows=[]
  for name,mov,en,v,dec in res:
    r='--'.join(name.split('--')[:2]); R[r][0]+=mov; R[r][1]+=en; R[r][2]=max(R[r][2],v); R[r][3]+=dec
    rows.append((name,mov,en,v,dec))
  for r,(m,e,v,d) in sorted(R.items()): print(r, f"moving-samples {m} cruise-samples {e} ({e/max(m,1):.0%}) vmax-cruise {v:.0f}mph hard-decel-samples {d}")
  import pickle; pickle.dump(rows,open(os.path.join(BASE,'data','qscan.pkl'),'wb'))
