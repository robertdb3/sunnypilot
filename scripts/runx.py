import os
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import sys,glob,subprocess
from concurrent.futures import ThreadPoolExecutor
fs=sorted(glob.glob(os.path.join(BASE,'logs/*/rlog.zst')))
chunks=[fs[i::4] for i in range(4)]
with ThreadPoolExecutor(4) as ex: list(ex.map(lambda c: subprocess.run([sys.executable,os.path.join(BASE,'scripts','extract.py'),*c],stdout=subprocess.DEVNULL), chunks))
