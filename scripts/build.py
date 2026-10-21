import os
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import pickle,glob,numpy as np
def series(d,name,src,sig):
  v=d['can'].get((name,src),[])
  if not v: return None,None
  return np.array([t for t,_ in v]), np.array([x[sig] for _,x in v],float)
def seg_frame(f):
  d=pickle.load(open(f,'rb')); cs=d['cs']
  if len(cs)==0: return None
  t=cs[:,0]; F={'t':t,'v':cs[:,1],'a':cs[:,2],'en':cs[:,3],'gas':cs[:,4],'brk':cs[:,5],'vset':cs[:,6],'stand':cs[:,7]}
  def put(key,name,src,sig):
    tt,vv=series(d,name,src,sig)
    F[key]= np.interp(t,tt,vv) if tt is not None else np.full_like(t,np.nan)
    if tt is not None:  # zero-order hold for discrete values
      idx=np.clip(np.searchsorted(tt,t)-1,0,len(tt)-1); F[key]=vv[idx]
  put('es_bp','ES_Brake',2,'Brake_Pressure'); put('es_bact','ES_Brake',2,'Cruise_Brake_Active'); put('es_bl','ES_Brake',2,'Cruise_Brake_Lights')
  put('es_cact','ES_Brake',2,'Cruise_Activated'); put('es_bfault','ES_Brake',2,'Cruise_Fault')
  put('es_thr','ES_Distance',2,'Cruise_Throttle'); put('es_d_bact','ES_Distance',2,'Cruise_Brake_Active'); put('es_stand','ES_Distance',2,'Standstill')
  put('es_rpm','ES_Status',2,'Cruise_RPM'); put('es_s_brake','ES_Status',2,'Brake'); put('es_s_act','ES_Status',2,'Cruise_Activated')
  put('bp_l','Brake_Pressure',0,'Brake_Pressure_Left'); put('bp_r','Brake_Pressure',0,'Brake_Pressure_Right')
  put('eng_tq','Engine',0,'Engine_Torque'); put('whl_tq','Engine',0,'Wheel_Torque'); put('rpm','Engine',0,'Engine_RPM')
  put('thr_cruise','Throttle',0,'Throttle_Cruise'); put('thr_combo','Throttle',0,'Throttle_Combo'); put('thr_pedal','Throttle',0,'Throttle_Pedal'); put('thr_body','Throttle',0,'Throttle_Body')
  put('gx','G_Sensor',0,'Longitudinal'); put('brkpedal','Brake_Pedal',0,'Brake_Pedal')
  put('cc_act','CruiseControl',0,'Cruise_Activated')
  if len(d['cc']): F['pitch']=np.interp(t,d['cc'][:,0],d['cc'][:,4])
  else: F['pitch']=np.full_like(t,np.nan)
  # echo of what was forwarded to the car (bus 0 TX) for comparison
  put('fw_bp','ES_Brake',128,'Brake_Pressure'); put('fw_thr','ES_Distance',128,'Cruise_Throttle'); put('fw_rpm','ES_Status',128,'Cruise_RPM')
  F['seg']=np.full(len(t),hash(f)%100000)
  return F
fr=[x for x in (seg_frame(f) for f in sorted(glob.glob(os.path.join(BASE,'out/*.pkl')))) if x is not None]
A={k:np.concatenate([f[k] for f in fr]) for k in fr[0]}
np.savez_compressed(os.path.join(BASE,'data','all.npz'),**{k:(v if k=='t' else v.astype(np.float32)) for k,v in A.items()}); print({k:A[k].shape for k in list(A)[:3]})
