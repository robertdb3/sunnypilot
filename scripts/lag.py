import os
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import numpy as np
A=dict(np.load(os.path.join(BASE,'data','all.npz'))); g=9.81; seg=A['seg']
aact=A['a']+g*np.sin(np.nan_to_num(A['pitch']))
ok=(A['en']>0)&(A['v']>2)&(A['gas']==0)&(A['brk']==0)&np.isfinite(A['pitch'])
def shift(x,L):
  if L==0: return x.copy()
  y=np.full_like(x,np.nan); y[:-L]=x[L:]; y[:-L][seg[:-L]!=seg[L:]]=np.nan; return y
print("lag(s)  corr(brake,-accel)  corr(throttle,accel)  corr(rpm,accel)")
for L in [0,10,20,30,40,50,60,80,100,150]:
  a2=shift(aact,L); m=ok&np.isfinite(a2)
  c=lambda k,s: np.corrcoef(A[k][m],s*a2[m])[0,1]
  print(f"{L/100:4.1f}    {c('es_bp',-1):.3f}              {c('es_thr',1):.3f}                {c('es_rpm',1):.3f}")
