import subprocess,sys,os,time,socket
WD=os.path.dirname(os.path.abspath(__file__))
PORT=11872
PYW=os.path.join(os.path.dirname(sys.executable),"pythonw.exe")
if not os.path.exists(PYW): PYW=sys.executable
def open_port():
    s=socket.socket(); r=s.connect_ex(("127.0.0.1",PORT)); s.close(); return r==0
log=open(os.path.join(WD,"server.log"),"a",encoding="utf-8")
proc=None
while True:
    if not open_port():
        # 仅当没有在跑的子进程才启动
        if proc is None or proc.poll() is not None:
            proc=subprocess.Popen([PYW,"-u","server.py",str(PORT)],cwd=WD,stdout=log,stderr=log,creationflags=0x00000008)
    time.sleep(8)
