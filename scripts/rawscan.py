import os
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import glob,os,zstandard,capnp,numpy as np,collections
capnp.remove_import_hook(); S=os.path.join(BASE,'schema')
log=capnp.load(f'{S}/log.capnp',imports=[S])
addrs=collections.defaultdict(lambda:[0,None,None]); esb=[]
for f in sorted(glob.glob(os.path.join(BASE,'logs/*/rlog.zst')))[::3]:
  data=zstandard.ZstdDecompressor().stream_reader(open(f,'rb')).read()
  for ev in log.Event.read_multiple_bytes(data,traversal_limit_in_words=2**63-1):
    if ev.which()!='can': continue
    for m in ev.can:
      if m.src!=2: continue
      b=np.frombuffer(bytes(m.dat),np.uint8); a=addrs[m.address]
      a[0]+=1
      if a[1] is None: a[1]=b.copy(); a[2]=np.zeros_like(b)
      if len(b)==len(a[1]): a[2]|=(b^a[1])
      if m.address==0x160: esb.append(int.from_bytes(bytes(m.dat),'little'))
print("bus-2 (camera side) addresses: count, len, bits that ever changed")
for ad,(n,first,chg) in sorted(addrs.items()):
  print(f"  0x{ad:03x} n={n:7d} len={len(first)} changed={chg.tobytes().hex()}")
esb=np.array(esb,dtype=object)
known=sum(1<<i for i in list(range(0,16))+[20,21,22,23]+[45]+list(range(48,51))+list(range(56,64)))
unk=0
for x in esb: unk|=(x & ~known)
print("ES_Brake undocumented bits that ever set:", [i for i in range(64) if unk>>i&1])
