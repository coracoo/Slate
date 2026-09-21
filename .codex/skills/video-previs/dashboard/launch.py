import subprocess,sys,os,time,urllib.request,json
WD=os.path.dirname(os.path.abspath(__file__))
PORT=sys.argv[1] if len(sys.argv)>1 else "11872"
log=open(os.path.join(WD,"server.log"),"w")
p=subprocess.Popen([sys.executable,"-u","server.py",PORT],cwd=WD,
    stdout=log,stderr=subprocess.STDOUT,
    creationflags=0x00000010)  # CREATE_NEW_CONSOLE, fully detached
print("pid",p.pid)
time.sleep(5)
try:
    r=json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/sources",timeout=5))
    print("OK sources",len(r))
except Exception as e:
    print("FAIL",e)

