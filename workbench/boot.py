import subprocess,sys,os
WD=os.path.dirname(os.path.abspath(__file__))
PYW=os.path.join(os.path.dirname(sys.executable),"pythonw.exe")
if not os.path.exists(PYW): PYW=sys.executable
log=open(os.path.join(WD,"server.log"),"a",encoding="utf-8")
subprocess.Popen([PYW,os.path.join(WD,"watch.py")],cwd=WD,stdout=log,stderr=log,creationflags=0x00000008|0x00000200)
print("watchdog booted")
