import os
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import numpy as np
A=dict(np.load(os.path.join(BASE,'data','all.npz'))); g=9.81; seg=A['seg']
aact=A['a']+g*np.sin(np.nan_to_num(A['pitch']))
def shift(x,L):
  y=np.full_like(x,np.nan); y[:-L]=x[L:]; y[:-L][seg[:-L]!=seg[L:]]=np.nan; return y
a_br=shift(aact,25); a_th=shift(aact,40)
ok=(A['en']>0)&(A['v']>2)&(A['gas']==0)&(A['brk']==0)&np.isfinite(A['pitch'])
br=ok&(A['es_bp']>0)&np.isfinite(a_br)
print("BRAKE: ES_Brake.Brake_Pressure -> grade-corrected accel 0.25s later (m/s^2)   [throttle value while braking]")
for lo,hi in [(1,25),(25,50),(50,100),(100,150),(150,200),(200,300),(300,400),(400,600),(600,1000)]:
  m=br&(A['es_bp']>=lo)&(A['es_bp']<hi)
  if m.sum()>20: print(f"  {lo:4d}-{hi:<4d} n={m.sum():6d}  accel p50 {np.median(a_br[m]):6.2f}  p10 {np.percentile(a_br[m],10):6.2f} p90 {np.percentile(a_br[m],90):6.2f}   thr p50 {np.median(A['es_thr'][m]):.0f}")
th=ok&(A['es_bp']==0)&np.isfinite(a_th)
print("THROTTLE (no brake): ES_Distance.Cruise_Throttle -> accel 0.4s later, by speed")
for vlo,vhi in [(2,10),(10,20),(20,30),(30,40)]:
  print(f" speed {vlo}-{vhi} m/s ({vlo*2.237:.0f}-{vhi*2.237:.0f} mph)")
  for lo,hi in [(0,1000),(1000,1500),(1500,2000),(2000,2500),(2500,3000),(3000,3500),(3500,4096)]:
    m=th&(A['es_thr']>=lo)&(A['es_thr']<hi)&(A['v']>=vlo)&(A['v']<vhi)
    if m.sum()>50: print(f"   thr {lo:4d}-{hi:<4d} n={m.sum():6d} accel p50 {np.median(a_th[m]):6.2f} (p10 {np.percentile(a_th[m],10):5.2f}, p90 {np.percentile(a_th[m],90):5.2f})")
